#!/usr/bin/env python3
import html
import hmac
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
import requests
import urllib.parse
import zipfile
import base64
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_orig_getaddrinfo = socket.getaddrinfo


def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if os.environ.get('BAIDU_OPENLIST_FORCE_IPV4', '1') != '0':
        family = socket.AF_INET
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = ipv4_getaddrinfo

BASE = Path(os.environ.get('BAIDU_OPENLIST_BASE', '/opt/baidu-openlist'))
BIN = BASE / 'bin' / 'BaiduPCS-Go'
DOWNLOADS = BASE / 'downloads'
JOBS = BASE / 'jobs'
LOGS = BASE / 'logs'
ZIP_CACHE = BASE / 'zip_cache'
GUEST_USAGE = BASE / 'guest_usage'
BROWSER_COOKIE_FILE = BASE / 'browser_cookie.txt'
REMOTE_ROOT = '/__openlist_tmp'
TTL_SECONDS = int(os.environ.get('BAIDU_OPENLIST_TTL_SECONDS', '86400'))
PORT = int(os.environ.get('BAIDU_OPENLIST_PORT', '9801'))
TOKEN = os.environ.get('BAIDU_OPENLIST_TOKEN', '')
ADMIN_PASSWORD = os.environ.get('BAIDU_OPENLIST_ADMIN_PASSWORD', TOKEN)
SESSION_SECRET = os.environ.get('BAIDU_OPENLIST_SESSION_SECRET', TOKEN or secrets.token_urlsafe(32))
GUEST_ENABLED = os.environ.get('BAIDU_OPENLIST_GUEST_ENABLED', '1') != '0'
GUEST_DAILY_LIMIT = int(os.environ.get('BAIDU_OPENLIST_GUEST_DAILY_LIMIT', '3'))
GUEST_GLOBAL_DAILY_LIMIT = int(os.environ.get('BAIDU_OPENLIST_GUEST_GLOBAL_DAILY_LIMIT', '100'))
BASE_PATH = os.environ.get('BAIDU_OPENLIST_BASE_PATH', '').rstrip('/')
OPENLIST_BAIDU_MOUNT = os.environ.get('BAIDU_OPENLIST_MOUNT', '/baidu')
OPENLIST_SITE_URL = os.environ.get('BAIDU_OPENLIST_SITE_URL', 'https://disk.example.com/openlist')
OPENLIST_API = os.environ.get('BAIDU_OPENLIST_API', 'http://127.0.0.1:5244/openlist')
OPENLIST_ADMIN_TOKEN = os.environ.get('BAIDU_OPENLIST_ADMIN_TOKEN', '')
OPENLIST_PAGE_SIZE = int(os.environ.get('BAIDU_OPENLIST_PAGE_SIZE', '200'))
ZIP_FREE_SPACE_BUFFER = int(os.environ.get('BAIDU_OPENLIST_ZIP_FREE_SPACE_BUFFER', str(2 * 1024 * 1024 * 1024)))
MAX_SERVER_ZIP_BYTES = int(os.environ.get('BAIDU_OPENLIST_MAX_SERVER_ZIP_BYTES', str(15 * 1024 * 1024 * 1024)))

for p in (DOWNLOADS, JOBS, LOGS, ZIP_CACHE, GUEST_USAGE):
    p.mkdir(parents=True, exist_ok=True)

q = queue.Queue()
zip_q = queue.Queue()
zip_inflight = set()
lock = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def job_file(job_id):
    return JOBS / f'{job_id}.json'


def safe_share(text):
    text = text.strip()
    m = re.search(r'https?://[^\s]+', text)
    return m.group(0) if m else text


def extract_code(text):
    patterns = [r'(?:提取码|访问码|密码)[:：\s]*([A-Za-z0-9]{4})', r'(?:pwd|pass|code)=([A-Za-z0-9]{4})']
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1)
    return ''


def load_job(job_id):
    with job_file(job_id).open('r', encoding='utf-8') as f:
        return json.load(f)


def save_job(job):
    tmp = job_file(job['id']).with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(job, f, ensure_ascii=False, indent=2)
    tmp.replace(job_file(job['id']))


def append_log(job, msg):
    line = f'[{datetime.now().strftime("%F %T")}] {msg}\n'
    with (LOGS / f"{job['id']}.log").open('a', encoding='utf-8', errors='replace') as f:
        f.write(line)


def run_cmd(job, args, timeout=None):
    append_log(job, '$ ' + ' '.join(args))
    proc = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    output = proc.stdout or ''
    if output:
        with (LOGS / f"{job['id']}.log").open('a', encoding='utf-8', errors='replace') as f:
            f.write(output)
            if not output.endswith('\n'):
                f.write('\n')
    if proc.returncode != 0:
        raise RuntimeError(f'command failed ({proc.returncode}): {" ".join(args)}')
    if '文件已存在' in output or '目录已存在' in output:
        return output
    fail_markers = ['失败', '错误', '请输入', '验证码']
    if any(marker in output for marker in fail_markers):
        raise RuntimeError(output.strip().splitlines()[-1] if output.strip() else 'command reported failure')
    return output


def run_pcs_rm(job, remote, attempts=5, delay=3):
    last_output = ''
    for attempt in range(1, attempts + 1):
        try:
            output = run_cmd(job, [str(BIN), 'rm', remote], timeout=90)
        except Exception as e:
            output = str(e)
        last_output = output or ''
        if any(marker in last_output for marker in ('操作成功', '文件不存在', 'No such file')):
            return last_output
        if '操作失败' not in last_output and '网络错误' not in last_output and 'timeout' not in last_output.lower():
            break
        if attempt < attempts:
            append_log(job, f'删除临时目录未确认成功，{delay} 秒后重试 ({attempt}/{attempts})')
            time.sleep(delay)
    raise RuntimeError(last_output.strip().splitlines()[-1] if last_output.strip() else '删除临时目录未确认成功')


def openlist_remove_path(openlist_path):
    parent, name = openlist_path.rstrip('/').rsplit('/', 1)
    resp = requests.post(
        OPENLIST_API.rstrip('/') + '/api/fs/remove',
        headers={'Authorization': OPENLIST_ADMIN_TOKEN, 'Content-Type': 'application/json'},
        json={'dir': parent or '/', 'names': [name]},
        timeout=60,
    )
    data = resp.json()
    if data.get('code') != 200:
        raise RuntimeError('OpenList 删除失败: ' + json.dumps(data, ensure_ascii=False))
    return data


def run_cmd_retry(job, args, attempts=3, delay=2, timeout=None):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            return run_cmd(job, args, timeout=timeout)
        except Exception as e:
            last_error = e
            if attempt >= attempts:
                break
            append_log(job, f'命令失败，{delay} 秒后重试 ({attempt}/{attempts}): {e}')
            time.sleep(delay)
    raise last_error


def delete_remote_temp(job, reason='任务结束'):
    remote = job.get('remote_dir') or f"{REMOTE_ROOT}/{job['id']}"
    if not remote:
        return False, 'missing remote_dir'
    try:
        append_log(job, f'{reason}，删除百度网盘临时目录: {remote}')
        try:
            run_pcs_rm(job, remote)
        except Exception as pcs_error:
            openlist_path = OPENLIST_BAIDU_MOUNT.rstrip('/') + remote
            append_log(job, f'BaiduPCS-Go 删除失败，改用 OpenList 删除 {openlist_path}: {pcs_error}')
            openlist_remove_path(openlist_path)
        job['remote_kept'] = False
        job['deleted_remote_at'] = now()
        job.pop('cleanup_error', None)
        save_job(job)
        return True, ''
    except Exception as e:
        job['remote_kept'] = True
        job['cleanup_error'] = str(e)
        save_job(job)
        append_log(job, f'删除百度网盘临时目录失败: {e}')
        return False, str(e)





def openlist_list(openlist_path, page=1, refresh=True):
    resp = requests.post(
        OPENLIST_API.rstrip('/') + '/api/fs/list',
        headers={'Authorization': OPENLIST_ADMIN_TOKEN, 'Content-Type': 'application/json'},
        json={'path': openlist_path, 'password': '', 'page': page, 'per_page': OPENLIST_PAGE_SIZE, 'refresh': refresh},
        timeout=30,
    )
    data = resp.json()
    if data.get('code') != 200:
        raise RuntimeError('OpenList 列目录失败: ' + json.dumps(data, ensure_ascii=False))
    data = data.get('data', {}) or {}
    return data.get('content') or [], int(data.get('total') or 0)


def walk_openlist_files(root_path, job_id=None):
    stack = [root_path.rstrip('/')]
    files = []
    scanned_dirs = 0
    while stack:
        cur = stack.pop()
        scanned_dirs += 1
        if job_id:
            update_progress(
                job_id,
                active=True,
                phase='扫描目录',
                current_file=cur,
                scanned_dirs=scanned_dirs,
                found_files=len(files),
            )
        page = 1
        while True:
            items, total = openlist_list(cur, page=page, refresh=(page == 1))
            for item in items:
                child = cur.rstrip('/') + '/' + item['name']
                if item.get('is_dir'):
                    stack.append(child)
                else:
                    files.append(child)
            if job_id:
                update_progress(
                    job_id,
                    active=True,
                    phase='扫描目录',
                    current_file=cur,
                    scanned_dirs=scanned_dirs,
                    found_files=len(files),
                )
            if not items or len(items) < OPENLIST_PAGE_SIZE or (total and page * OPENLIST_PAGE_SIZE >= total):
                break
            page += 1
    return files

def get_openlist_raw_url(openlist_path):
    if not OPENLIST_ADMIN_TOKEN:
        raise RuntimeError('缺少 OpenList admin token')
    resp = requests.post(
        OPENLIST_API.rstrip('/') + '/api/fs/get',
        headers={'Authorization': OPENLIST_ADMIN_TOKEN, 'Content-Type': 'application/json'},
        json={'path': openlist_path, 'password': '', 'refresh': True},
        timeout=20,
    )
    data = resp.json()
    if data.get('code') != 200 and openlist_path.count('/') > 2:
        parent = openlist_path.rsplit('/', 1)[0]
        parts = [x for x in parent.split('/') if x]
        refresh_paths = []
        for i in range(1, len(parts) + 1):
            refresh_paths.append('/' + '/'.join(parts[:i]))
        try:
            for rp in refresh_paths:
                requests.post(
                    OPENLIST_API.rstrip('/') + '/api/fs/list',
                    headers={'Authorization': OPENLIST_ADMIN_TOKEN, 'Content-Type': 'application/json'},
                    json={'path': rp, 'password': '', 'page': 1, 'per_page': 0, 'refresh': True},
                    timeout=20,
                )
        except Exception:
            pass
        resp = requests.post(
            OPENLIST_API.rstrip('/') + '/api/fs/get',
            headers={'Authorization': OPENLIST_ADMIN_TOKEN, 'Content-Type': 'application/json'},
            json={'path': openlist_path, 'password': '', 'refresh': True},
            timeout=20,
        )
        data = resp.json()
    if data.get('code') != 200:
        raise RuntimeError('OpenList 获取下载地址失败: ' + json.dumps(data, ensure_ascii=False))
    raw = data.get('data', {}).get('raw_url')
    if not raw:
        raise RuntimeError('OpenList 未返回 raw_url')
    raw = re.sub(r'^https?://[^/]+/openlist', OPENLIST_API.rstrip(' /'), raw)
    return raw


def has_enough_zip_space(required_bytes):
    if not required_bytes:
        return True, shutil.disk_usage(ZIP_CACHE).free
    free = shutil.disk_usage(ZIP_CACHE).free
    return free > int(required_bytes * 1.15) + ZIP_FREE_SPACE_BUFFER, free

def share_surl(share_url):
    m = re.search(r'/s/1([^?/#]+)', share_url)
    if not m:
        raise RuntimeError('无法识别百度分享 surl')
    return m.group(1)


def web_transfer(job, remote_dir):
    if not BROWSER_COOKIE_FILE.exists():
        raise RuntimeError('缺少浏览器 Cookie: /opt/baidu-openlist/browser_cookie.txt')
    cookie = BROWSER_COOKIE_FILE.read_text(encoding='utf-8').strip()
    share = job['share_url']
    code = job.get('code') or ''
    surl = share_surl(share)
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/143 Safari/537.36',
        'Referer': 'https://pan.baidu.com/',
        'Cookie': cookie,
    })
    append_log(job, f'网页接口校验分享: surl={surl}')
    verify = session.post(
        'https://pan.baidu.com/share/verify',
        params={'surl': surl, 't': str(int(time.time() * 1000)), 'channel': 'chunlei', 'web': '1', 'app_id': '250528', 'clienttype': '0'},
        data={'pwd': code, 'vcode': '', 'vcode_str': ''},
        headers={'Referer': share},
        timeout=25,
    )
    try:
        verify_data = verify.json()
    except Exception:
        raise RuntimeError('分享校验返回异常: ' + verify.text[:200])
    if verify_data.get('errno') not in (0, '0'):
        raise RuntimeError('分享校验失败: ' + json.dumps(verify_data, ensure_ascii=False))
    sekey = urllib.parse.unquote(verify_data.get('randsk', ''))
    page = session.get(share, headers={'Referer': 'https://pan.baidu.com/share/init?surl=' + surl}, timeout=25)
    text = page.text
    if '/login?' in page.url:
        raise RuntimeError('浏览器 Cookie 已失效，需要重新复制 Cookie')
    def pick(key):
        m = re.search(rf'"{key}"\s*:\s*"?([^",}}]+)', text)
        return m.group(1).strip('"') if m else ''
    shareid = pick('shareid')
    share_uk = pick('share_uk')
    fsids = [int(x) for x in re.findall(r'"fs_id"\s*:\s*"?([^",}]+)', text)]
    if not (shareid and share_uk and fsids):
        raise RuntimeError('无法解析分享文件元数据')
    append_log(job, f'网页接口解析成功: shareid={shareid}, share_uk={share_uk}, fsids={fsids[:10]}')
    transfer = session.post(
        'https://pan.baidu.com/share/transfer',
        params={'shareid': shareid, 'from': share_uk, 'sekey': sekey, 'ondup': 'newcopy', 'async': '1', 'channel': 'chunlei', 'web': '1', 'app_id': '250528', 'bdstoken': '', 'clienttype': '0'},
        data={'fsidlist': json.dumps(fsids), 'path': remote_dir},
        headers={'Referer': share, 'Origin': 'https://pan.baidu.com'},
        timeout=40,
    )
    try:
        transfer_data = transfer.json()
    except Exception:
        raise RuntimeError('转存接口返回异常: ' + transfer.text[:300])
    append_log(job, '网页转存返回: ' + json.dumps(transfer_data, ensure_ascii=False)[:2000])
    if transfer_data.get('errno') not in (0, '0'):
        raise RuntimeError('网页转存失败: ' + (transfer_data.get('show_msg') or json.dumps(transfer_data, ensure_ascii=False)))
    files = []
    for item in transfer_data.get('extra', {}).get('list', []):
        to_path = item.get('to')
        if to_path:
            files.append(to_path)
    job['transferred_files'] = files
    return transfer_data

def worker():
    while True:
        job_id = q.get()
        try:
            job = load_job(job_id)
            job.update(status='running', started_at=now(), remote_kept=False)
            save_job(job)
            update_progress(job_id, active=True, phase='正在创建百度临时目录', sent_bytes=0, current_index=0)
            append_log(job, '开始任务')

            out_dir = DOWNLOADS / job_id
            out_dir.mkdir(parents=True, exist_ok=True)
            remote_dir = f'{REMOTE_ROOT}/{job_id}'
            share = job['share_url']
            code = job.get('code') or ''

            run_cmd_retry(job, [str(BIN), 'mkdir', remote_dir], attempts=4, delay=3, timeout=45)
            update_progress(job_id, active=True, phase='正在转存百度分享', current_file=share)
            web_transfer(job, remote_dir)
            job['remote_dir'] = remote_dir
            job['status'] = 'transferred'
            save_job(job)
            openlist_path = OPENLIST_BAIDU_MOUNT.rstrip('/') + remote_dir
            update_progress(job_id, active=True, phase='正在刷新 OpenList 文件列表', current_file=openlist_path)
            openlist_url = OPENLIST_SITE_URL.rstrip('/') + openlist_path
            file_paths = transferred_file_paths(job)
            openlist_direct_urls = [OPENLIST_SITE_URL.rstrip() + '/d' + OPENLIST_BAIDU_MOUNT.rstrip('/') + urllib.parse.quote(x, safe='/') for x in file_paths]
            direct_urls = [app_url('/download/' + job_id)] if len(file_paths) == 1 else []
            job.update(
                status='ready',
                finished_at=now(),
                remote_dir=remote_dir,
                openlist_path=openlist_path,
                openlist_url=openlist_url,
                direct_urls=direct_urls,
                openlist_direct_urls=openlist_direct_urls,
                expires_at=time.time()+TTL_SECONDS,
                remote_kept=True,
            )
            append_log(job, f'转存完成，请用 OpenList 访问: {openlist_url}')
            save_job(job)
            update_progress(job_id, active=False, phase='已准备好，可以下载', sent_bytes=0)
        except Exception as e:
            try:
                job = load_job(job_id)
                if job.get('status') != 'download_failed':
                    job.update(status='failed', finished_at=now(), error=str(e))
                save_job(job)
                append_log(job, '失败: ' + str(e))
                delete_remote_temp(job, '任务失败')
            except Exception:
                pass
        finally:
            q.task_done()


def update_progress(job_id, **fields):
    try:
        job = load_job(job_id)
        progress = job.get('download_progress') or {}
        progress.update(fields)
        progress['updated_at'] = now()
        job['download_progress'] = progress
        save_job(job)
    except Exception:
        pass


def progress_html(job):
    p = job.get('download_progress') or {}
    status = job.get('status') or ''
    total_files = int(p.get('total_files') or 0)
    current_index = int(p.get('current_index') or 0)
    total_bytes = int(p.get('total_bytes') or 0)
    sent = int(p.get('sent_bytes') or 0)
    if status in ('ready', 'zip_ready'):
        pct = 100
        label = '100% · 已准备好'
    elif status in ('failed', 'zip_failed', 'download_failed'):
        pct = 100
        label = '失败'
    elif status in ('downloaded_deleted', 'expired'):
        pct = 100
        label = '已完成并清理'
    elif status == 'queued':
        pct = 5
        label = '排队中'
    elif status == 'running':
        pct = 18
        label = str(p.get('phase') or '处理中')
    elif total_bytes > 0:
        pct = min(100, int(sent * 100 / total_bytes))
        label = f'{pct}% · {sent/1024/1024:.1f}MB / {total_bytes/1024/1024:.1f}MB'
    elif total_files > 0:
        pct = min(100, int(current_index * 100 / total_files))
        label = f'{current_index}/{total_files} 个文件'
    elif p.get('phase') == '扫描目录':
        pct = 8
        label = f'已扫 {int(p.get("scanned_dirs") or 0)} 个目录 · 发现 {int(p.get("found_files") or 0)} 个文件'
    else:
        pct = 0
        label = '等待开始'
    current = html.escape(str(p.get('current_file') or p.get('phase') or '准备中'))
    return (f'<div class="progress" style="--pct:{pct}%"><span></span></div>'
            f'<div class="progress-meta"><span>{label}</span><span>{current}</span></div>')


def transferred_file_paths(job):
    items = job.get('transferred_files') or []
    files = []
    for item in items:
        if isinstance(item, str) and '.' in item.rsplit('/', 1)[-1]:
            files.append(item)
        elif isinstance(item, dict) and not item.get('is_dir') and item.get('path'):
            files.append(item['path'])
    return files


def transferred_has_dirs(job):
    return any(isinstance(x, dict) and x.get('is_dir') for x in (job.get('transferred_files') or []))


def job_file_items(job):
    return transferred_file_paths(job)


def job_has_dirs(job):
    return transferred_has_dirs(job) or (job.get('openlist_path') and not job_file_items(job))


def build_zip(job_id):
    job = load_job(job_id)
    root_path = job.get('openlist_path') or (OPENLIST_BAIDU_MOUNT.rstrip('/') + job.get('remote_dir', ''))
    zip_path = ZIP_CACHE / f'{job_id}.zip'
    tmp_path = ZIP_CACHE / f'{job_id}.zip.part'
    if tmp_path.exists():
        tmp_path.unlink()
    update_progress(job_id, active=True, phase='扫描目录', sent_bytes=0, current_index=0, found_files=0, scanned_dirs=0)
    file_paths = walk_openlist_files(root_path, job_id=job_id)
    if not file_paths:
        raise RuntimeError('没有从 OpenList 扫描到可下载文件')
    raw_map = {}
    total_zip_bytes = 0
    for idx, fp in enumerate(file_paths, 1):
        update_progress(job_id, phase='获取下载地址', current_index=idx, total_files=len(file_paths), current_file=fp)
        raw = get_openlist_raw_url(fp)
        raw_map[fp] = raw
        try:
            meta = requests.head(raw, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            total_zip_bytes += int(meta.headers.get('Content-Length') or 0)
        except Exception:
            pass
    if total_zip_bytes > MAX_SERVER_ZIP_BYTES:
        raise RuntimeError(
            f'文件夹约 {total_zip_bytes/1024/1024/1024:.1f}GB，超过服务器打包上限 '
            f'{MAX_SERVER_ZIP_BYTES/1024/1024/1024:.0f}GB。请从 OpenList 目录里直接下载文件，避免服务器磁盘被打满。'
        )
    ok, free_bytes = has_enough_zip_space(total_zip_bytes)
    if not ok:
        raise RuntimeError(
            f'文件夹约 {total_zip_bytes/1024/1024/1024:.1f}GB，服务器可用空间约 {free_bytes/1024/1024/1024:.1f}GB，'
            '不适合先打包 ZIP。请从 OpenList 目录里分文件下载，或换更大的磁盘。'
        )
    update_progress(job_id, phase='写入 ZIP', total_files=len(file_paths), total_bytes=total_zip_bytes, sent_bytes=0)
    root_prefix = root_path.rstrip('/') + '/'
    sent = 0
    with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for idx, fp in enumerate(file_paths, 1):
            update_progress(job_id, current_index=idx, current_file=fp, sent_bytes=sent)
            upstream = requests.get(raw_map[fp], stream=True, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
            if upstream.status_code >= 400 or 'text/html' in upstream.headers.get('Content-Type', ''):
                raise RuntimeError(f'下载源异常: {fp}: HTTP {upstream.status_code}, content-type={upstream.headers.get("Content-Type", "")}')
            arcname = fp[len(root_prefix):] if fp.startswith(root_prefix) else fp.strip('/')
            with zf.open(arcname, 'w') as dest:
                for chunk in upstream.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        dest.write(chunk)
                        sent += len(chunk)
                        if sent % (8 * 1024 * 1024) < len(chunk):
                            update_progress(job_id, sent_bytes=sent)
            upstream.close()
    tmp_path.replace(zip_path)
    job = load_job(job_id)
    job.update(status='zip_ready', zip_path=str(zip_path), zip_size=zip_path.stat().st_size)
    save_job(job)
    update_progress(job_id, active=False, phase='ZIP 已准备好，可以下载', sent_bytes=sent, total_bytes=sent)
    append_log(job, f'ZIP 已准备好: {zip_path} ({zip_path.stat().st_size} bytes)')


def zip_worker():
    while True:
        job_id = zip_q.get()
        try:
            with lock:
                zip_inflight.add(job_id)
            build_zip(job_id)
        except Exception as e:
            try:
                job = load_job(job_id)
                job.update(status='zip_failed', error=str(e))
                save_job(job)
                delete_remote_temp(job, 'ZIP 准备失败')
                update_progress(job_id, active=False, phase='ZIP 失败: ' + str(e))
                append_log(job, 'ZIP 失败: ' + str(e))
            except Exception:
                pass
        finally:
            with lock:
                zip_inflight.discard(job_id)
            zip_q.task_done()


def janitor():
    while True:
        try:
            cutoff = time.time()
            for jf in JOBS.glob('*.json'):
                try:
                    job = json.loads(jf.read_text(encoding='utf-8'))
                    exp = job.get('expires_at')
                    should_retry_remote = bool(job.get('remote_kept') and job.get('remote_dir'))
                    should_expire = bool(exp and exp < cutoff)
                    if should_retry_remote and job.get('status') not in ('running', 'queued', 'zipping'):
                        delete_remote_temp(job, '后台重试清理')
                        job = json.loads(jf.read_text(encoding='utf-8'))
                    if should_expire:
                        remote = job.get('remote_dir')
                        if remote:
                            delete_remote_temp(job, '任务过期')
                            job = json.loads(jf.read_text(encoding='utf-8'))
                        path = DOWNLOADS / job['id']
                        shutil.rmtree(path, ignore_errors=True)
                        job['status'] = 'expired'
                        job['expired_at'] = now()
                        save_job(job)
                except Exception:
                    continue
        finally:
            time.sleep(1800)


APPLE_CSS = """
:root {
  --bg: #f5f5f7;
  --surface: rgba(255,255,255,0.72);
  --surface-solid: #ffffff;
  --border: rgba(0,0,0,0.08);
  --text: #1d1d1f;
  --text-muted: #6e6e73;
  --accent: #0071e3;
  --accent-hover: #0077ed;
  --accent-pressed: #006edb;
  --shadow: 0 1px 2px rgba(0,0,0,0.04), 0 8px 24px rgba(0,0,0,0.06);
}
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-size: 15px; line-height: 1.5;
}
.container { max-width: 980px; margin: 0 auto; padding: 48px 24px 80px; }
.hero { text-align: center; margin-bottom: 32px; }
.hero h1 {
  font-size: 40px; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 8px;
  background: linear-gradient(180deg, #1d1d1f 0%, #515154 100%);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text;
}
.hero p { color: var(--text-muted); font-size: 17px; margin: 0; }
.card {
  background: var(--surface);
  backdrop-filter: saturate(180%) blur(20px);
  -webkit-backdrop-filter: saturate(180%) blur(20px);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 24px;
  box-shadow: var(--shadow);
  margin-bottom: 20px;
}
.card h2 { font-size: 19px; font-weight: 600; margin: 0 0 16px; letter-spacing: -0.01em; }
textarea, input[type="text"], input:not([type]) {
  width: 100%; padding: 12px 14px; font: inherit; color: var(--text);
  background: var(--surface-solid); border: 1px solid var(--border);
  border-radius: 12px; outline: none;
  transition: border-color .15s, box-shadow .15s;
  resize: vertical;
}
textarea:focus, input:focus { border-color: var(--accent); box-shadow: 0 0 0 4px rgba(0,113,227,0.15); }
textarea { min-height: 110px; }
.form-row { margin-bottom: 12px; }
button, .btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 10px 20px; font: inherit; font-weight: 500;
  color: #fff; background: var(--accent);
  border: 0; border-radius: 980px; cursor: pointer;
  text-decoration: none;
  transition: background .15s, transform .05s;
}
button:hover, .btn:hover { background: var(--accent-hover); }
button:active, .btn:active { background: var(--accent-pressed); transform: scale(0.98); }
.btn.secondary { background: rgba(0,0,0,0.06); color: var(--text); }
.btn.secondary:hover { background: rgba(0,0,0,0.1); }
.note { color: var(--text-muted); font-size: 13px; margin: 0; }
table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 14px; }
th, td { padding: 14px 12px; text-align: left; border-bottom: 1px solid var(--border); vertical-align: middle; }
th { font-weight: 600; color: var(--text-muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; }
tr:last-child td { border-bottom: 0; }
td a { color: var(--accent); text-decoration: none; }
td a:hover { text-decoration: underline; }
.mono { font-family: "SF Mono", ui-monospace, "Cascadia Code", Menlo, Consolas, monospace; font-size: 13px; }
.badge {
  display: inline-block; padding: 3px 10px; border-radius: 980px;
  font-size: 12px; font-weight: 500;
  background: rgba(142,142,147,0.18); color: #3a3a3c;
}
.badge.running { background: rgba(0,113,227,0.14); color: #0058b3; }
.badge.transferred { background: rgba(48,176,199,0.18); color: #007a8a; }
.badge.ready { background: rgba(52,199,89,0.18); color: #1e8e3e; }
.badge.downloaded_deleted { background: rgba(52,199,89,0.14); color: #1e8e3e; }
.badge.failed, .badge.download_failed, .badge.downloaded_cleanup_failed { background: rgba(255,59,48,0.14); color: #c62828; }
.badge.expired { background: rgba(142,142,147,0.18); color: #6e6e73; }
pre {
  background: #1d1d1f; color: #e8e8ed;
  padding: 16px 20px; border-radius: 14px;
  overflow: auto; margin: 0;
  font: 13px/1.55 "SF Mono", ui-monospace, Menlo, Consolas, monospace;
}
pre.json { background: #f5f5f7; color: var(--text); border: 1px solid var(--border); }
.row-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.progress { height: 10px; background: rgba(0,0,0,0.08); border-radius: 999px; overflow: hidden; }
.progress > span { display:block; height:100%; background: var(--accent); width: var(--pct, 0%); transition: width .25s ease; }
.progress-meta { display:flex; justify-content:space-between; gap:12px; color:var(--text-muted); font-size:13px; margin-top:8px; }
.back {
  display: inline-flex; align-items: center; gap: 4px;
  color: var(--accent); text-decoration: none;
  margin-bottom: 20px; font-size: 14px;
}
.back:hover { text-decoration: underline; }
.empty { text-align: center; color: var(--text-muted); padding: 32px 0; font-size: 14px; }
.error-card { border-color: rgba(255,59,48,0.3) !important; }
.error-card h2 { color: #c62828; }
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #000;
    --surface: rgba(28,28,30,0.72);
    --surface-solid: #1c1c1e;
    --border: rgba(255,255,255,0.1);
    --text: #f5f5f7;
    --text-muted: #98989d;
    --shadow: 0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.4);
  }
  .hero h1 {
    background: linear-gradient(180deg, #f5f5f7 0%, #a1a1a6 100%);
    -webkit-background-clip: text; background-clip: text;
  }
  .btn.secondary { background: rgba(255,255,255,0.1); color: var(--text); }
  .btn.secondary:hover { background: rgba(255,255,255,0.16); }
  pre.json { background: #1c1c1e; color: #e8e8ed; }
}
"""


def render_page(title, body):
    return (
        '<!doctype html><html lang="zh-CN"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="theme-color" content="#f5f5f7" media="(prefers-color-scheme: light)">'
        '<meta name="theme-color" content="#000000" media="(prefers-color-scheme: dark)">'
        f'<title>{html.escape(title)}</title>'
        f'<style>{APPLE_CSS}</style>'
        f'</head><body><div class="container">{body}</div></body></html>'
    )


def app_url(target):
    return (BASE_PATH or '') + target


def b64url(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def sign_session(role, exp, subject=''):
    payload = f'{role}:{exp}:{subject}'
    sig = hmac.new(SESSION_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
    return b64url(f'{payload}:{sig}'.encode('utf-8'))


def verify_session(value):
    if not value:
        return '', ''
    try:
        padded = value + '=' * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode('ascii')).decode('utf-8')
        role, exp_s, subject, sig = raw.rsplit(':', 3)
        exp = int(exp_s)
        if exp < int(time.time()):
            return '', ''
        payload = f'{role}:{exp}:{subject}'
        expected = hmac.new(SESSION_SECRET.encode('utf-8'), payload.encode('utf-8'), hashlib.sha256).hexdigest()
        if hmac.compare_digest(sig, expected) and role in ('admin', 'guest'):
            return role, subject
    except Exception:
        return '', ''
    return '', ''


def parse_cookies(header):
    cookies = {}
    for part in (header or '').split(';'):
        if '=' in part:
            key, value = part.split('=', 1)
            cookies[key.strip()] = value.strip()
    return cookies


def guest_usage_file(quota_key, day=None):
    day = day or datetime.now().strftime('%Y%m%d')
    safe_id = re.sub(r'[^A-Za-z0-9_-]', '', quota_key)[:80] or 'unknown'
    return GUEST_USAGE / f'{day}-{safe_id}.json'


def guest_global_usage_file(day=None):
    day = day or datetime.now().strftime('%Y%m%d')
    return GUEST_USAGE / f'{day}-global.json'


def load_guest_usage(quota_key):
    path = guest_usage_file(quota_key)
    if not path.exists():
        return {'count': 0, 'jobs': []}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'count': 0, 'jobs': []}


def save_guest_usage(quota_key, usage):
    path = guest_usage_file(quota_key)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(usage, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_guest_global_usage():
    path = guest_global_usage_file()
    if not path.exists():
        return {'count': 0, 'jobs': []}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {'count': 0, 'jobs': []}


def save_guest_global_usage(usage):
    path = guest_global_usage_file()
    tmp = path.with_suffix('.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        json.dump(usage, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def consume_guest_quota(quota_key, job_id):
    with lock:
        usage = load_guest_usage(quota_key)
        global_usage = load_guest_global_usage()
        count = int(usage.get('count') or 0)
        global_count = int(global_usage.get('count') or 0)
        if global_count >= GUEST_GLOBAL_DAILY_LIMIT:
            return False, max(0, GUEST_DAILY_LIMIT - count), max(0, GUEST_GLOBAL_DAILY_LIMIT - global_count), 'global'
        if count >= GUEST_DAILY_LIMIT:
            return False, max(0, GUEST_DAILY_LIMIT - count), max(0, GUEST_GLOBAL_DAILY_LIMIT - global_count), 'user'
        usage['count'] = count + 1
        jobs = usage.get('jobs') or []
        jobs.append(job_id)
        usage['jobs'] = jobs[-200:]
        save_guest_usage(quota_key, usage)
        global_usage['count'] = global_count + 1
        global_jobs = global_usage.get('jobs') or []
        global_jobs.append(job_id)
        global_usage['jobs'] = global_jobs[-1000:]
        save_guest_global_usage(global_usage)
        return True, max(0, GUEST_DAILY_LIMIT - usage['count']), max(0, GUEST_GLOBAL_DAILY_LIMIT - global_usage['count']), ''


def guest_quota_remaining(quota_key):
    usage = load_guest_usage(quota_key)
    return max(0, GUEST_DAILY_LIMIT - int(usage.get('count') or 0))


def guest_global_quota_remaining():
    usage = load_guest_global_usage()
    return max(0, GUEST_GLOBAL_DAILY_LIMIT - int(usage.get('count') or 0))


def stable_hash(value):
    return hashlib.sha256((SESSION_SECRET + ':' + value).encode('utf-8')).hexdigest()[:32]


def today_key():
    return datetime.now().strftime('%Y%m%d')


def guest_usage_summary(day=None):
    day = day or today_key()
    rows = []
    for p in GUEST_USAGE.glob(f'{day}-*.json'):
        key = p.stem[len(day) + 1:]
        if key == 'global':
            continue
        try:
            usage = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        rows.append({
            'key': key,
            'count': int(usage.get('count') or 0),
            'jobs': usage.get('jobs') or [],
            'mtime': p.stat().st_mtime,
        })
    return sorted(rows, key=lambda x: (x['count'], x['mtime']), reverse=True)


def role_can_access_job(role, subject, job):
    return role == 'admin' or (role == 'guest' and subject and job.get('owner_role') == 'guest' and job.get('owner_id') == subject)


def task_rows_html(role, subject):
    rows = []
    for jf in sorted(JOBS.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
        job = json.loads(jf.read_text(encoding='utf-8'))
        if not role_can_access_job(role, subject, job):
            continue
        jid = html.escape(job['id'])
        status = job.get('status', '')
        if job.get('direct_urls') and len(job.get('direct_urls', [])) == 1:
            link_cell = f'<a class="btn" href="{html.escape(job["direct_urls"][0])}" download>直接下载</a>'
        elif job.get('openlist_url'):
            link_cell = (
                '<div class="row-actions">'
                f'<a class="btn" href="{app_url("/download/" + jid)}" download>下载 ZIP</a>'
                f'<a class="btn secondary" href="{html.escape(job.get("openlist_url", ""))}">打开目录</a>'
                '</div>'
            )
        elif job.get('error'):
            link_cell = f'<span class="mono" style="color:#c62828">{html.escape(str(job.get("error","")))[:120]}</span>'
        else:
            link_cell = '<span style="color:var(--text-muted)">—</span>'
        rows.append(
            f'<tr><td><a class="mono" href="{app_url("/job/" + jid)}">{jid}</a></td>'
            f'<td><span class="badge {html.escape(status)}">{html.escape(status) or "—"}</span></td>'
            f'<td>{progress_html(job)}</td>'
            f'<td class="mono" style="color:var(--text-muted)">{html.escape(fmt_time(job.get("created_at","")))}</td>'
            f'<td>{link_cell}</td></tr>'
        )
        if len(rows) >= 30:
            break
    return ''.join(rows) if rows else '<tr><td colspan="5" class="empty">还没有任务，提交一个试试吧</td></tr>'


def admin_login_page(error=''):
    error_html = f'<section class="card error-card"><p>{html.escape(error)}</p></section>' if error else ''
    body = (
        '<header class="hero">'
        '<h1>管理员登录</h1>'
        '<p>查看公益转存额度、用户使用统计和后台任务。</p>'
        '</header>'
        f'{error_html}'
        '<section class="card">'
        f'<form method="post" action="{app_url("/admin/login")}">'
        '<div class="form-row"><input type="password" name="password" placeholder="管理员密码"></div>'
        '<div class="row-actions"><button type="submit">登录</button></div>'
        '</form>'
        '</section>'
    )
    return render_page('管理员登录 · OpenList Share Bridge', body)


def bridge_home_page(role, subject, submitted='', quota_key=''):
    def href(target):
        return app_url(target)
    token_q = ''
    is_guest = role == 'guest'
    quota = guest_quota_remaining(quota_key) if is_guest else None
    global_quota = guest_global_quota_remaining()
    rows_html = task_rows_html(role, subject)
    guest_note = ''
    if is_guest:
        guest_note = (
            '<section class="card">'
            '<h2>今日额度</h2>'
            f'<p class="note">公益池今日剩余 <strong><span id="global-quota">{global_quota}</span> / {GUEST_GLOBAL_DAILY_LIMIT}</strong> 次；'
            f'你今天还可以使用 <strong><span id="guest-quota">{quota}</span> / {GUEST_DAILY_LIMIT}</strong> 次。</p>'
            '</section>'
        )
    submitted_note = ''
    if submitted:
        submitted_note = (
            '<section class="card">'
            '<h2>任务已提交</h2>'
            f'<p class="note">任务 <span class="mono">{html.escape(submitted)}</span> 已加入队列，下面会自动刷新进度。</p>'
            '</section>'
        )
    refresh_script = (
        '<script>'
        'async function refreshTasks(){'
        'try{'
        f'const r=await fetch("{href("/tasks")}",{{headers:{{"Accept":"application/json"}}}});'
        'if(!r.ok)return;'
        'const data=await r.json();'
        'const body=document.getElementById("task-rows");'
        'if(body)body.innerHTML=data.html;'
        'const quota=document.getElementById("guest-quota");'
        'if(quota&&data.quota!==null)quota.textContent=data.quota;'
        'const globalQuota=document.getElementById("global-quota");'
        'if(globalQuota&&data.global_quota!==null)globalQuota.textContent=data.global_quota;'
        '}catch(e){}'
        '}'
        'setInterval(refreshTasks,2000);'
        'setTimeout(refreshTasks,700);'
        'if(location.search.includes("submitted=")){history.replaceState(null,"",location.pathname);}'
        '</script>'
    )
    body = (
        '<header class="hero">'
        '<h1>公益转存</h1>'
        '<p>粘贴百度网盘分享链接，临时转存，直接下载；完成后自动清理。</p>'
        '</header>'
        f'{guest_note}'
        f'{submitted_note}'
        f'<form class="card" method="post" action="{href("/submit")}{token_q}">'
        '<h2>新建任务</h2>'
        '<div class="form-row"><textarea name="text" placeholder="粘贴百度分享链接，可包含提取码"></textarea></div>'
        '<div class="form-row"><input name="code" placeholder="提取码（留空自动识别）"></div>'
        '<div class="row-actions"><button type="submit">提交下载</button>'
        '<span class="note">请只转存你有权访问的文件。临时资源会自动清理。</span></div>'
        '</form>'
        '<section class="card">'
        '<h2>最近任务</h2>'
        '<table><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead>'
        f'<tbody id="task-rows">{rows_html}</tbody></table>'
        '</section>'
        f'{refresh_script}'
    )
    return render_page('Baidu OpenList', body)


def guest_page(subject='', quota_key=''):
    body = (
        '<header class="hero">'
        '<h1>游客体验</h1>'
        f'<p>游客每天可以体验 {GUEST_DAILY_LIMIT} 次真实转存和下载。</p>'
        '</header>'
        '<section class="card">'
        '<h2>开始体验</h2>'
        f'<p class="note">今天还可以体验 {guest_quota_remaining(quota_key)} / {GUEST_DAILY_LIMIT} 次。游客只能看到自己的任务。</p>'
        f'<div class="row-actions"><a class="btn" href="{app_url("/")}">进入下载页面</a><a class="btn secondary" href="{app_url("/logout")}">退出游客模式</a></div>'
        '</section>'
    )
    return render_page('游客体验 · OpenList Share Bridge', body)


def admin_dashboard_page():
    global_usage = load_guest_global_usage()
    global_count = int(global_usage.get('count') or 0)
    user_rows = []
    for item in guest_usage_summary()[:100]:
        last_jobs = ', '.join(html.escape(x) for x in item['jobs'][-5:])
        user_rows.append(
            '<tr>'
            f'<td class="mono">{html.escape(item["key"])}</td>'
            f'<td>{item["count"]}</td>'
            f'<td>{len(item["jobs"])}</td>'
            f'<td class="mono">{last_jobs or "—"}</td>'
            '</tr>'
        )
    users_html = ''.join(user_rows) if user_rows else '<tr><td colspan="4" class="empty">今天还没有游客使用记录</td></tr>'
    body = (
        '<header class="hero">'
        '<h1>管理员后台</h1>'
        '<p>查看公益转存今日额度和游客使用情况。</p>'
        '<p><a class="back" href="' + app_url('/') + '">返回首页</a> · <a class="back" href="' + app_url('/logout') + '">退出登录</a></p>'
        '</header>'
        '<section class="card">'
        '<h2>今日总额度</h2>'
        f'<p class="note">今天已使用 <strong>{global_count}</strong> 次，剩余 <strong>{max(0, GUEST_GLOBAL_DAILY_LIMIT - global_count)}</strong> / {GUEST_GLOBAL_DAILY_LIMIT} 次。</p>'
        '</section>'
        '<section class="card">'
        '<h2>游客使用排行</h2>'
        '<table><thead><tr><th>用户指纹</th><th>使用次数</th><th>任务数</th><th>最近任务</th></tr></thead>'
        f'<tbody>{users_html}</tbody></table>'
        '</section>'
        '<section class="card">'
        '<h2>全部任务</h2>'
        '<table><thead><tr><th>任务</th><th>状态</th><th>进度</th><th>创建时间</th><th>操作</th></tr></thead>'
        f'<tbody>{task_rows_html("admin", "")}</tbody></table>'
        '</section>'
    )
    return render_page('管理员后台 · OpenList Share Bridge', body)


def fmt_time(iso):
    if not iso:
        return ''
    try:
        return iso.split('.')[0].split('+')[0].replace('T', ' ')
    except Exception:
        return iso


class Handler(BaseHTTPRequestHandler):
    def current_session(self):
        cookies = parse_cookies(self.headers.get('Cookie', ''))
        role, subject = verify_session(cookies.get('olsb_session', ''))
        if role:
            return role, subject
        if not TOKEN and not ADMIN_PASSWORD:
            return 'admin', ''
        qs = parse_qs(urlparse(self.path).query)
        if qs.get('token', [''])[0] == TOKEN or self.headers.get('X-Token') == TOKEN:
            return 'admin', ''
        return '', ''

    def current_role(self):
        return self.current_session()[0]

    def auth_ok(self):
        return self.current_role() == 'admin'

    def set_session(self, role, max_age, subject=''):
        exp = int(time.time()) + max_age
        value = sign_session(role, exp, subject)
        self.send_header('Set-Cookie', f'olsb_session={value}; Path={BASE_PATH or "/"}; Max-Age={max_age}; HttpOnly; SameSite=Lax')

    def require_job_access(self, job):
        role, subject = self.current_session()
        return role_can_access_job(role, subject, job)

    def client_ip(self):
        forwarded = self.headers.get('X-Forwarded-For', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
        real_ip = self.headers.get('X-Real-IP', '').strip()
        if real_ip:
            return real_ip
        return self.client_address[0] if self.client_address else ''

    def guest_quota_key(self):
        ua = self.headers.get('User-Agent', '')
        return stable_hash(f'{self.client_ip()}|{ua}')

    def clear_session(self):
        self.send_header('Set-Cookie', f'olsb_session=; Path={BASE_PATH or "/"}; Max-Age=0; HttpOnly; SameSite=Lax')

    def normalize_path(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if BASE_PATH and path.startswith(BASE_PATH + '/'):
            path = path[len(BASE_PATH):]
        elif BASE_PATH and path == BASE_PATH:
            path = '/'
        return parsed, path

    def redirect_to(self, target):
        self.send_response(303)
        self.send_header('Location', (BASE_PATH or '') + target)
        self.end_headers()

    def send_html(self, body, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(body.encode('utf-8'))

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def do_HEAD(self):
        parsed, path = self.normalize_path()
        if path in ('/', '/login', '/guest'):
            self.send_response(200 if path != '/guest' or GUEST_ENABLED else 404)
        else:
            self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()

    def do_GET(self):
        parsed, path = self.normalize_path()
        def href(target):
            return (BASE_PATH or '') + target
        if path == '/login':
            self.redirect_to('/admin/login'); return
        if path == '/admin/login':
            self.send_html(admin_login_page()); return
        if path == '/admin':
            role, _ = self.current_session()
            if role != 'admin':
                self.redirect_to('/admin/login'); return
            self.send_html(admin_dashboard_page()); return
        if path == '/guest' and GUEST_ENABLED:
            role, subject = self.current_session()
            if role != 'guest':
                self.redirect_to('/'); return
            self.send_html(guest_page(subject, self.guest_quota_key())); return
        if path == '/logout':
            self.send_response(303)
            self.clear_session()
            self.send_header('Location', href('/'))
            self.end_headers()
            return
        role, subject = self.current_session()
        if path == '/':
            if role not in ('guest', 'admin'):
                self.send_response(303)
                guest_id = secrets.token_urlsafe(18)
                self.set_session('guest', 24 * 3600, guest_id)
                self.send_header('Location', href('/'))
                self.end_headers()
                return
            if role == 'admin':
                subject = ''
            submitted = parse_qs(parsed.query).get('submitted', [''])[0]
            self.send_html(bridge_home_page(role if role == 'guest' else 'guest', subject, submitted, self.guest_quota_key()))
        elif path == '/tasks':
            if role not in ('guest', 'admin'):
                self.send_json({'html': '<tr><td colspan="5" class="empty">请刷新页面开始使用</td></tr>', 'quota': None, 'global_quota': guest_global_quota_remaining()}, 401); return
            public_role = 'guest'
            public_subject = subject if role == 'guest' else ''
            quota = guest_quota_remaining(self.guest_quota_key())
            self.send_json({'html': task_rows_html(public_role, public_subject), 'quota': quota, 'global_quota': guest_global_quota_remaining()})
        elif path.startswith('/progress/'):
            jid = path.rsplit('/', 1)[-1]
            try:
                job = load_job(jid)
                if not self.require_job_access(job):
                    self.send_html(render_page('无权访问', '<header class="hero"><h1>403</h1><p>你不能访问这个任务。</p></header>'), 403); return
                token_q = ''
                p = job.get('download_progress') or {}
                active = bool(p.get('active'))
                token_q_raw = ''
                if job.get('status') == 'zip_ready':
                    start = f'<p><a class="btn" href="{href("/download_zip/" + jid)}{token_q}" download>下载已准备好的 ZIP</a></p>'
                    auto = ''
                elif job.get('status') == 'zip_failed':
                    safe_error = html.escape(str(job.get('error') or 'ZIP 准备失败'))
                    start = f'<section class="card error-card"><h2>ZIP 准备失败</h2><pre>{safe_error}</pre></section>'
                    auto = ''
                elif job_has_dirs(job):
                    auto = '<meta http-equiv="refresh" content="1">'
                    start = f'<iframe name="prepare_frame" style="display:none"></iframe><script>window.onload=function(){{document.getElementById("prep").submit();}}</script><form id="prep" method="get" action="{href("/prepare_zip/" + jid)}" target="prepare_frame">'
                    if TOKEN:
                        start += f'<input type="hidden" name="token" value="{html.escape(TOKEN)}">'
                    start += '</form>'
                else:
                    auto = '<meta http-equiv="refresh" content="1">' if active else ''
                    start = ''
                    if not active and job.get('status') not in ('downloaded_deleted', 'downloaded_cleanup_failed'):
                        start = f'<iframe name="download_frame" style="display:none"></iframe><script>window.onload=function(){{document.getElementById("dl").submit();}}</script><form id="dl" method="get" action="{href("/download/" + jid)}" target="download_frame">'
                        if TOKEN:
                            start += f'<input type="hidden" name="token" value="{html.escape(TOKEN)}">'
                        start += '</form>'
                body = (
                    auto + f'<a class="back" href="{href("/")}{token_q}">? ??</a>'
                    '<header class="hero" style="text-align:left;margin-bottom:24px">'
                    f'<h1>????</h1><p class="mono">{html.escape(jid)}</p></header>'
                    f'<section class="card"><h2>??</h2>{progress_html(job)}<p class="note" style="margin-top:12px">??????????????????????????????</p></section>'
                    f'{start}'
                )
                self.send_html(render_page('???? ? Baidu OpenList', body))
            except Exception:
                self.send_html(render_page('???', '<header class="hero"><h1>404</h1><p>???????</p></header>'), 404)
        elif path.startswith('/prepare_zip/'):
            jid = path.rsplit('/', 1)[-1]
            try:
                job = load_job(jid)
                if not self.require_job_access(job):
                    self.send_html('forbidden', 403); return
                if job.get('status') == 'zip_ready':
                    self.send_html('ready'); return
                if job.get('status') == 'zip_failed':
                    self.send_html('failed: ' + html.escape(str(job.get('error') or 'ZIP 准备失败')), 409); return
                with lock:
                    already = jid in zip_inflight
                    if not already:
                        zip_inflight.add(jid)
                if not already:
                    job['status'] = 'zipping'
                    save_job(job)
                    update_progress(jid, active=True, phase='准备 ZIP', sent_bytes=0, current_index=0)
                    zip_q.put(jid)
                self.send_html('started')
            except Exception as e:
                self.send_html(html.escape(str(e)), 500)
        elif path.startswith('/download_zip/'):
            jid = path.rsplit('/', 1)[-1]
            try:
                job = load_job(jid)
                if not self.require_job_access(job):
                    self.send_html('forbidden', 403); return
                zip_path = Path(job.get('zip_path') or '')
                if not zip_path.exists():
                    self.send_html('ZIP 还没准备好', 404); return
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Length', str(zip_path.stat().st_size))
                self.send_header('Content-Disposition', 'attachment; filename*=UTF-8\'\'' + urllib.parse.quote(zip_path.name))
                self.end_headers()
                sent = 0
                with zip_path.open('rb') as f:
                    while True:
                        chunk = f.read(1024 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        sent += len(chunk)
                if sent >= zip_path.stat().st_size:
                    zip_path.unlink(missing_ok=True)
                    delete_remote_temp(job, 'ZIP 下载完成')
                    job = load_job(jid)
                    job.update(status='downloaded_deleted', downloaded_at=now(), remote_kept=False)
                    save_job(job)
                    update_progress(jid, active=False, phase='ZIP 已下载，临时文件已删除', sent_bytes=sent, total_bytes=sent)
            except (BrokenPipeError, ConnectionResetError) as e:
                try:
                    job = load_job(jid)
                    delete_remote_temp(job, 'ZIP 下载中断')
                    job.update(status='download_failed', error='浏览器下载中断: ' + str(e))
                    save_job(job)
                except Exception:
                    pass
            except Exception as e:
                try:
                    job = load_job(jid)
                    delete_remote_temp(job, 'ZIP 下载失败')
                    job.update(status='download_failed', error=str(e))
                    save_job(job)
                except Exception:
                    pass
                self.send_html(html.escape(str(e)), 500)
        elif path.startswith('/download/'):
            jid = path.rsplit('/', 1)[-1]
            try:
                job = load_job(jid)
                if not self.require_job_access(job):
                    self.send_html('forbidden', 403); return
                files = transferred_file_paths(job)
                if len(files) != 1:
                    self.send_response(303)
                    self.send_header('Location', href('/progress/' + jid))
                    self.end_headers()
                    return
                update_progress(jid, active=True, phase='下载中', sent_bytes=0, current_index=0)
                zip_mode = False
                if len(files) == 1:
                    openlist_path = OPENLIST_BAIDU_MOUNT.rstrip('/') + files[0]
                    raw = get_openlist_raw_url(openlist_path)
                sent = 0
                expected = 0
                completed = False
                if not zip_mode:
                    upstream = requests.get(raw, stream=True, headers={'User-Agent': 'Mozilla/5.0'}, timeout=30)
                    if upstream.status_code >= 400 or 'text/html' in upstream.headers.get('Content-Type', ''):
                        raise RuntimeError(f'上游下载失败: HTTP {upstream.status_code}, content-type={upstream.headers.get("Content-Type", "")}')
                    filename = files[0].rsplit('/', 1)[-1]
                    self.send_response(200)
                    self.send_header('Content-Type', upstream.headers.get('Content-Type', 'application/octet-stream'))
                    if upstream.headers.get('Content-Length'):
                        self.send_header('Content-Length', upstream.headers['Content-Length'])
                    self.send_header('Content-Disposition', 'attachment; filename*=UTF-8\'\'' + urllib.parse.quote(filename))
                    self.end_headers()
                    expected = int(upstream.headers.get('Content-Length') or '0')
                    update_progress(jid, phase='下载中', total_files=1, current_index=1, current_file=filename, total_bytes=expected, sent_bytes=0)
                    try:
                        for chunk in upstream.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                self.wfile.write(chunk)
                                sent += len(chunk)
                                if sent % (8 * 1024 * 1024) < len(chunk):
                                    update_progress(jid, sent_bytes=sent)
                        completed = (expected == 0 or sent >= expected)
                    finally:
                        upstream.close()
                if completed:
                    delete_remote_temp(job, f'浏览器下载完成，已发送 {sent} 字节')
                    update_progress(jid, active=False, phase='下载完成，临时目录已删除', sent_bytes=sent)
                    job = load_job(jid)
                    job.update(status='downloaded_deleted', downloaded_at=now(), remote_kept=False)
                    save_job(job)
                else:
                    append_log(job, f'下载未确认完成，已发送 {sent} / {expected} 字节，删除临时目录')
                    delete_remote_temp(job, '下载未确认完成')
                    job.update(status='download_failed', error=f'下载未确认完成，已发送 {sent} / {expected} 字节')
                    save_job(job)
            except (BrokenPipeError, ConnectionResetError) as e:
                try:
                    job = load_job(jid)
                    append_log(job, '浏览器下载中断，删除临时目录: ' + str(e))
                    delete_remote_temp(job, '浏览器下载中断')
                    job.update(status='download_failed', error='浏览器下载中断: ' + str(e))
                    save_job(job)
                except Exception:
                    pass
            except Exception as e:
                try:
                    job = load_job(jid)
                    delete_remote_temp(job, '下载失败')
                    job.update(status='download_failed', error=str(e))
                    save_job(job)
                except Exception:
                    pass
                self.send_html(html.escape(str(e)), 500)
        elif path.startswith('/job/'):
            jid = path.rsplit('/',1)[-1]
            try:
                job = load_job(jid)
                if not self.require_job_access(job):
                    self.send_html(render_page('无权访问', '<header class="hero"><h1>403</h1><p>你不能访问这个任务。</p></header>'), 403); return
                log = (LOGS / f'{jid}.log').read_text(encoding='utf-8', errors='replace') if (LOGS / f'{jid}.log').exists() else ''
                status = job.get('status', '')
                token_q = ''
                actions = []
                if job.get('direct_urls') and len(job.get('direct_urls', [])) == 1:
                    actions.append(f'<a class="btn" href="{html.escape(job["direct_urls"][0])}" download>直接下载文件</a>')
                elif job.get('openlist_url'):
                    actions.append(f'<a class="btn" href="{href("/download/" + jid)}{token_q}" download>下载 ZIP</a>')
                if job.get('openlist_url'):
                    actions.append(f'<a class="btn secondary" href="{html.escape(job["openlist_url"])}">打开目录</a>')
                files_card = ''
                if job.get('direct_urls') and len(job.get('direct_urls', [])) > 1:
                    items = ''.join(
                        f'<li style="margin:8px 0"><a class="btn" href="{html.escape(u)}" download>{html.escape(urllib.parse.unquote(u.rsplit("/",1)[-1]))}</a></li>'
                        for u in job.get('direct_urls', [])
                    )
                    files_card = f'<section class="card"><h2>文件列表</h2><ul style="list-style:none;padding:0;margin:0">{items}</ul></section>'
                if actions:
                    action_card = f'<section class="card"><h2>下载</h2><div class="row-actions">{"".join(actions)}</div></section>'
                elif job.get('error'):
                    action_card = f'<section class="card error-card"><h2>出错了</h2><pre>{html.escape(str(job.get("error","")))}</pre></section>'
                else:
                    action_card = '<section class="card"><p class="note">任务还在处理中，刷新页面查看进展。</p></section>'
                body = (
                    f'<a class="back" href="{href("/")}{token_q}">← 返回</a>'
                    '<header class="hero" style="text-align:left;margin-bottom:24px">'
                    f'<h1 class="mono" style="font-size:28px">{html.escape(jid)}</h1>'
                    f'<p><span class="badge {html.escape(status)}">{html.escape(status) or "—"}</span>'
                    f' &nbsp;<span class="note">创建于 {html.escape(fmt_time(job.get("created_at","")))}</span></p>'
                    '</header>'
                    f'{action_card}'
                    f'{files_card}'
                    '<section class="card"><h2>任务信息</h2>'
                    f'<pre class="json">{html.escape(json.dumps(job, ensure_ascii=False, indent=2))}</pre></section>'
                    '<section class="card"><h2>日志</h2>'
                    f'<pre>{html.escape(log[-20000:]) if log else "（无日志）"}</pre></section>'
                )
                self.send_html(render_page(jid + ' · Baidu OpenList', body))
            except Exception:
                self.send_html(render_page('未找到', '<header class="hero"><h1>404</h1><p>找不到这个任务</p></header>'), 404)
        else:
            self.send_html('not found', 404)

    def do_POST(self):
        parsed, path = self.normalize_path()
        length = int(self.headers.get('Content-Length', 0))
        data = parse_qs(self.rfile.read(length).decode('utf-8', errors='replace'))
        if path == '/login':
            self.redirect_to('/admin/login'); return
        if path == '/admin/login':
            password = data.get('password', [''])[0]
            if ADMIN_PASSWORD and hmac.compare_digest(password, ADMIN_PASSWORD):
                self.send_response(303)
                self.set_session('admin', 7 * 86400)
                self.send_header('Location', (BASE_PATH or '') + '/admin')
                self.end_headers()
                return
            self.send_html(admin_login_page('密码不正确'), 401)
            return
        if path == '/guest' and GUEST_ENABLED:
            self.send_response(303)
            guest_id = secrets.token_urlsafe(18)
            self.set_session('guest', 24 * 3600, guest_id)
            self.send_header('Location', (BASE_PATH or '') + '/')
            self.end_headers()
            return
        role, subject = self.current_session()
        if role not in ('admin', 'guest'):
            self.redirect_to('/'); return
        if path != '/submit':
            self.send_html('not found', 404); return
        if role == 'guest':
            if not GUEST_ENABLED:
                self.send_html('guest disabled', 403); return
            if not subject:
                self.redirect_to('/login'); return
        text = data.get('text', [''])[0]
        code = data.get('code', [''])[0].strip() or extract_code(text)
        share = safe_share(text)
        if not share.startswith('http'):
            self.send_html('没有识别到分享链接', 400); return
        jid = uuid.uuid4().hex[:12]
        if role == 'guest':
            quota_key = self.guest_quota_key()
            allowed, remaining, global_remaining, limit_reason = consume_guest_quota(quota_key, jid)
            if not allowed:
                title = '今日公益额度已用完' if limit_reason == 'global' else '你的今日次数已用完'
                message = (
                    f'公益池今天最多可转存 {GUEST_GLOBAL_DAILY_LIMIT} 次，请明天再来。'
                    if limit_reason == 'global'
                    else f'每个用户每天最多可以体验 {GUEST_DAILY_LIMIT} 次，请明天再来。'
                )
                body = (
                    f'<header class="hero"><h1>{title}</h1>'
                    f'<p>{message}</p></header>'
                    f'<section class="card"><a class="btn secondary" href="{app_url("/")}">返回</a></section>'
                )
                self.send_html(render_page('游客额度已用完', body), 429); return
        else:
            quota_key = ''
            remaining = None
        job = {
            'id': jid,
            'status': 'queued',
            'created_at': now(),
            'share_url': share,
            'code': code,
            'owner_role': role,
            'owner_id': subject if role == 'guest' else '',
        }
        if role == 'guest':
            job['guest_remaining_today'] = remaining
            job['guest_global_remaining_today'] = global_remaining
            job['guest_quota_key'] = quota_key
        save_job(job)
        q.put(jid)
        self.send_response(303)
        self.send_header('Location', f'{BASE_PATH}/?submitted={urllib.parse.quote(jid)}' if BASE_PATH else f'/?submitted={urllib.parse.quote(jid)}')
        self.end_headers()


if __name__ == '__main__':
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=zip_worker, daemon=True).start()
    threading.Thread(target=janitor, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
