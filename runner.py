"""GitHub Actions 실행 진입점.

예전에는 이 코드가 news.yml 안에 heredoc 파이썬으로 박혀 있었다. 그래서
로컬 테스트도, 린트도, diff 검토도 불가능했다. 파일로 분리해 워크플로는
`python runner.py` 한 줄만 남긴다.

발송 기록은 저장소의 newsbot-state.json 에 보관한다. API 키는 저장하지 않는다.
"""
import base64
import json
import logging
import os
import sys
import traceback
import time
from datetime import datetime
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import news_bot_v4 as bot

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
LOG = logging.getLogger('runner')

REQUIRED = ('NAVER_CLIENT_ID', 'NAVER_CLIENT_SECRET',
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID', 'GH_TOKEN')

ENDPOINT = ('https://api.github.com/repos/' + os.environ.get('GITHUB_REPOSITORY', '')
            + '/contents/newsbot-state.json')
BRANCH = os.environ.get('STATE_BRANCH', 'main')
_sha = None


# Contents API 는 같은 파일에 연속으로 쓰면 ref 갱신이 충돌해 500을 낸다.
# 한 회차에 발송 건수만큼 PUT 이 나가므로 최소 간격을 둔다.
MIN_PUT_INTERVAL = 1.5
RETRY_CODES = (429, 500, 502, 503, 504)
_last_put = 0.0


def github(method, payload=None, attempts=4):
    global _last_put
    url = ENDPOINT + ('?ref=' + quote(BRANCH, safe='') if method == 'GET' else '')
    headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
               'Accept': 'application/vnd.github+json',
               'User-Agent': 'policy-news-bot'}
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None:
        headers['Content-Type'] = 'application/json'
    for attempt in range(attempts):
        if method != 'GET':
            gap = MIN_PUT_INTERVAL - (time.monotonic() - _last_put)
            if gap > 0:
                time.sleep(gap)
        try:
            with urlopen(Request(url, data=body, headers=headers, method=method),
                         timeout=30) as response:
                result = json.load(response)
            _last_put = time.monotonic()
            return result
        except HTTPError as error:
            # 404 는 최초 실행 판별에 쓰이므로 그대로 올린다.
            if error.code not in RETRY_CODES or attempt == attempts - 1:
                raise
            wait = 2 ** attempt
            LOG.warning('GitHub API %s %d / %d초 후 재시도 (%d/%d)',
                        method, error.code, wait, attempt + 1, attempts - 1)
            time.sleep(wait)
        except OSError as error:
            if attempt == attempts - 1:
                raise
            LOG.warning('GitHub API 연결 실패 (%s) / 재시도', type(error).__name__)
            time.sleep(2 ** attempt)
    raise RuntimeError('GitHub API 재시도 소진')


def load_state():
    """저장된 발송 기록을 읽는다. 없으면 로컬 sqlite 에서 1회 이전한다."""
    global _sha
    try:
        saved = github('GET')
    except HTTPError as error:
        if error.code != 404:
            raise
        state = {'version': 1, 'articles': [], 'deliveries': [], 'slots': []}
        seed = bot.ROOT / 'live-rules.sqlite3'
        if seed.exists():
            import sqlite3
            with sqlite3.connect(seed.as_uri() + '?mode=ro', uri=True) as old:
                state['articles'] = old.execute(
                    'SELECT id,titlekey,data,analysis FROM articles').fetchall()
                state['deliveries'] = old.execute(
                    'SELECT id,destination,status FROM deliveries').fetchall()
                state['slots'] = old.execute(
                    'SELECT slot FROM slots ORDER BY rowid DESC LIMIT 72').fetchall()[::-1]
            LOG.info('기존 로컬 발송 기록을 이전했습니다.')
        return state
    _sha = saved['sha']
    state = json.loads(base64.b64decode(saved['content']))
    if state.get('version') != 1:
        raise RuntimeError('알 수 없는 기록 형식')
    return state


def build_db(state):
    db = bot.database(':memory:')
    for row in state['articles']:
        article = json.loads(row[2])
        if bot.recent(article, 48):
            db.execute('INSERT OR IGNORE INTO articles VALUES (?,?,?,?)', row)
    for row in state['deliveries']:
        if db.execute('SELECT 1 FROM articles WHERE id=?', (row[0],)).fetchone():
            db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?)', row)
    for row in state['slots'][-72:]:
        db.execute('INSERT OR IGNORE INTO slots VALUES (?)', row)
    db.commit()
    return db


def make_persist(db):
    def persist():
        global _sha
        # 발송·예약된 기사만 남긴다. API 키는 저장하지 않는다.
        snapshot = {
            'version': 1,
            'articles': db.execute(
                'SELECT DISTINCT a.id,a.titlekey,a.data,a.analysis '
                'FROM articles a JOIN deliveries d ON a.id=d.id').fetchall(),
            'deliveries': db.execute('SELECT id,destination,status FROM deliveries').fetchall(),
            'slots': db.execute(
                'SELECT slot FROM slots ORDER BY rowid DESC LIMIT 72').fetchall()[::-1],
        }
        payload = {'message': 'Update news delivery history [skip ci]', 'branch': BRANCH,
                   'content': base64.b64encode(
                       json.dumps(snapshot, ensure_ascii=False).encode()).decode()}
        if _sha:
            payload['sha'] = _sha
        _sha = github('PUT', payload)['content']['sha']
    return persist


def alert(text):
    """실패를 조용히 넘기지 않는다. 이 알림이 없으면 이틀 죽어 있어도 모른다."""
    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat = os.getenv('TELEGRAM_CHAT_ID', '')
    if not token or not chat:
        return
    try:
        bot.request('https://api.telegram.org/bot' + token + '/sendMessage',
                    {'chat_id': chat, 'text': text,
                     'link_preview_options': {'is_disabled': True}})
    except Exception:
        LOG.error('실패 알림 발송도 실패했습니다.')


def main():
    for name in REQUIRED:
        if not os.getenv(name, '').strip():
            raise RuntimeError('필수 Secret 누락: ' + name)

    event = os.environ.get('EVENT_NAME', '')
    rule = os.environ.get('SCHEDULE_RULE', '')
    manual_track = os.environ.get('RUN_DAILY', 'none').strip().lower()

    tracks = []
    run_combined = False
    if event == 'schedule':
        track = bot.DAILY_SCHEDULE.get(rule)
        if track == 'combined':
            run_combined = True
        elif track:
            tracks = [track]
    elif event == 'workflow_dispatch':
        if manual_track == 'combined':
            run_combined = True
        elif manual_track == 'all':
            tracks = list(bot.DAILY_TRACKS)
        elif manual_track == 'both':  # 이전 워크플로와의 하위 호환용 별칭
            tracks = ['foreign', 'game']
        elif manual_track in bot.DAILY_TRACKS:
            tracks = [manual_track]
    daily_only = bool(tracks) or run_combined

    db = build_db(load_state())
    persist = make_persist(db)
    persist()  # 텔레그램 발송 전에 쓰기 권한을 먼저 확인한다.

    original_request = bot.request
    sent = {'count': 0}

    def durable_request(url, payload=None, headers=None):
        if url.startswith('https://api.telegram.org/bot') and url.endswith('/sendMessage'):
            # bot.send 가 로컬에 'sending' 을 기록한 뒤다. 먼저 원격에 저장한다.
            persist()
            result = original_request(url, payload, headers)
            sent['count'] += 1
            return result
        return original_request(url, payload, headers)

    bot.request = durable_request

    cfg = bot.load_config()
    args = SimpleNamespace(demo=False, ai=False, send=True, daily=None,
                           watch=(event == 'schedule'))
    LOG.info('실행 %s / 검색어 %d개 / 일일 리포트 %s',
             event or 'local', len(cfg['queries']),
             'combined' if run_combined else (','.join(tracks) or '없음'))
    run_error = None
    try:
        if not daily_only:
            bot.run(args, cfg, db)
        if run_combined:
            bot.run_daily_combined(args, cfg, db)
        for track in tracks:
            bot.run_daily(args, cfg, db, track_name=track)
    except Exception as error:
        run_error = error
    try:
        persist()
    except Exception as error:
        # 발송은 끝났고 마지막 기록 저장만 실패한 경우다. 발송 직전 저장이
        # 이미 끝나 있어 중복 발송은 생기지 않으므로 실행 실패로 취급하지 않는다.
        if run_error is None and sent['count']:
            LOG.error('기록 저장 실패 (%s) / 발송 %d건은 완료',
                      type(error).__name__, sent['count'])
            alert('ℹ️ 뉴스봇 기록 저장 실패\n'
                  f"발송 {sent['count']}건은 정상 완료됐습니다.\n"
                  '중복 발송은 생기지 않으며 다음 회차에 자동 복구됩니다.')
            return
        run_error = run_error or error
    if run_error:
        raise run_error
    print(f"실행 완료. 발송 {sent['count']}건, 기록을 저장했습니다.")


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        traceback.print_exc()
        alert('⚠️ 뉴스봇 실행 실패\n'
              + type(error).__name__
              + '\n' + datetime.now(bot.KST).strftime('%m/%d %H:%M') + ' KST'
              + '\nActions 로그를 확인하세요.')
        raise SystemExit(1)
