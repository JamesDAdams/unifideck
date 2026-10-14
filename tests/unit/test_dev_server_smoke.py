import json, urllib.request, subprocess, time, os, sys

def test_dev_server_smoke(tmp_path):
    # start server
    env = dict(os.environ)
    env['DECKY_DEV_SERVER_PORT'] = '8799'
    proc = subprocess.Popen([sys.executable, 'dev-server/server.py'], cwd=str(__import__('pathlib').Path(__file__).resolve().parents[2]), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1.0)
    try:
        # health
        with urllib.request.urlopen('http://127.0.0.1:8799/health') as r:
            h = json.loads(r.read())
        assert h['status'] == 'ok'
        # ingest
        data = json.dumps({'source':'t','lines':['a','b']}).encode()
        req = urllib.request.Request('http://127.0.0.1:8799/ingest', data=data, headers={'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(req) as r:
            body = json.loads(r.read())
        assert body['accepted'] == 2
        # logs
        with urllib.request.urlopen('http://127.0.0.1:8799/logs?limit=10') as r:
            body = json.loads(r.read())
        assert body['count'] == 2
    finally:
        proc.terminate()
        proc.wait(timeout=5)
