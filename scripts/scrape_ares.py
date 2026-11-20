# -*- coding: utf-8 -*-
"""museum-jiaoyou: fetch ares 25 sensors -> monitor.json (stdlib only).
Run by GitHub Actions every 10 minutes.
"""
import base64, datetime as dt, json, os, sys, urllib.parse, urllib.request, http.cookiejar

BASE = os.environ.get('ARES_BASE', 'http://ares.jumor.cn')
USER = os.environ.get('ARES_USER', 'tzqbwg')
PWD = os.environ.get('ARES_PASS', 'jumor.cn')
OUT_DIR = os.environ.get('OUT_DIR', '.')
INTERVAL = int(os.environ.get('INTERVAL', '600'))
MON = os.path.join(OUT_DIR, 'monitor.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0'


def now_bj():
    return dt.datetime.utcnow() + dt.timedelta(hours=8)


def log(msg):
    print('[%s] %s' % (now_bj().strftime('%Y-%m-%d %H:%M:%S'), msg))


def b64(s):
    return base64.b64encode(s.encode('utf-8')).decode('ascii')


def op(path, data=None):
    body = urllib.parse.urlencode(data).encode('utf-8') if data else None
    req = urllib.request.Request(BASE + path, data=body)
    req.add_header('User-Agent', UA)
    req.add_header('Referer', BASE + '/ares/a/history/index')
    req.add_header('Origin', BASE)
    req.add_header('X-Requested-With', 'XMLHttpRequest')
    if data:
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    with OPENER.open(req, timeout=60) as r:
        return r.read().decode('utf-8', 'ignore')


def pull_once():
    today = now_bj()
    dfmt = today.strftime('%Y-%m-%d')
    gte = (today - dt.timedelta(days=12)).strftime('%Y-%m-%d 00:00')
    lte = dfmt + ' 23:59'

    zones = json.loads(op('/ares/a/eros/zone/locationTreeData'))
    targets = [z for z in zones if z.get('pId') in ('tzqbwg02', 'tzqbwg01')]

    pts = []
    fail = 0
    for z in targets:
        rows = []
        for page in range(1, 6):
            d = {'locationId': z['id'], 'createTime_gte': gte, 'createTime_lte': lte,
                 'pageNo': str(page), 'pageSize': '500'}
            try:
                j = json.loads(op('/ares/a/history/listData', d))
            except Exception as e:
                log('fail %s: %s' % (z.get('name'), e))
                fail += 1
                break
            lst = j.get('list') or []
            rows.extend(lst)
            if len(lst) < 500:
                break
        if not rows:
            continue
        recs = []
        for rec in rows:
            sm = rec.get('sensorMap') or {}
            try:
                t = float(sm['33']['resultValue'])
                h = float(sm['32']['resultValue'])
            except Exception:
                continue
            co2 = None
            if '36' in sm:
                try:
                    co2 = round(float(sm['36']['resultValue']))
                except Exception:
                    co2 = None
            recs.append((rec['createTime'], t, h, co2))
        if not recs:
            continue
        recs.sort()
        name = z.get('name') or ''
        short = name.split('-')[0]
        no = ''.join(c for c in name.split('-')[-1] if c.isdigit())
        grp = '库房' if z.get('pId') == 'tzqbwg02' else '展厅'
        if '二氧化碳' in name or '综合监测' in name:
            grp = '环境'
        last = recs[-1]
        w24 = [x for x in recs
               if x[0] >= (today - dt.timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')]
        hourly = {}
        for ts, t, h, _ in w24:
            hourly.setdefault(ts[11:13], []).append((t, h))
        Hs = [[hh,
               round(sum(p[0] for p in hourly[hh]) / len(hourly[hh]), 2),
               round(sum(p[1] for p in hourly[hh]) / len(hourly[hh]), 2)]
              for hh in sorted(hourly)]
        daily = {}
        for ts, t, h, _ in recs:
            k = daily.setdefault(ts[:10], {'t': [], 'h': []})
            k['t'].append(t)
            k['h'].append(h)
        D = []
        for dd in sorted(daily)[-8:]:
            g = daily[dd]
            D.append([dd[5:], round(min(g['t']), 1), round(max(g['t']), 1),
                      round(min(g['h']), 1), round(max(g['h']), 1),
                      round(sum(g['t']) / len(g['t']), 1),
                      round(sum(g['h']) / len(g['h']), 1)])
        t, h, co2 = round(last[1], 1), round(last[2], 1), last[3]
        if grp == '库房':
            sd = [18, 22, 45, 55]
        elif grp == '展厅':
            sd = [15, 25, 40, 60]
        else:
            sd = [15, 25, 0, 100]
        ot = max(sd[0] - t, t - sd[1], 0)
        oh = max(sd[2] - h, h - sd[3], 0)
        st = 'ok' if (ot == 0 and oh == 0) else ('warn' if max(ot, oh) <= 2 else 'bad')
        pts.append({
            'i': ('k' if grp == '库房' else 'e' if grp == '环境' else 'z') + no,
            'n': short, 'no': no, 'g': grp, 't': t, 'h': h,
            'ts': last[0][5:16], 'st': st, 'sd': sd,
            's': Hs, 'd': D, 'c': co2,
            'rng': [round(min(x[1] for x in recs), 1), round(max(x[1] for x in recs), 1),
                    round(min(x[2] for x in recs), 1), round(max(x[2] for x in recs), 1)],
            'n_': len(recs), 'span': [recs[0][0][:16], last[0][:16]],
        })

    pts.sort(key=lambda p: ({'库房': 0, '展厅': 1, '环境': 2}[p['g']], int(p['no'])))
    if len(pts) < 20:
        log('too few points (%d), skip write' % len(pts))
        return False

    meta = {'src': BASE, 'pull': today.strftime('%Y-%m-%d %H:%M'),
            'iv': '%d 分钟' % max(1, INTERVAL // 60),
            'note': '湿度=传感32 · 温度=传感33 · CO2=传感36'}
    body = json.dumps({'meta': meta, 'pts': pts}, ensure_ascii=False, separators=(',', ':'))
    tmp = MON + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(body)
    os.replace(tmp, MON)
    cnt = {'ok': 0, 'warn': 0, 'bad': 0}
    for p in pts:
        cnt[p['st']] += 1
    log('done: %d pts, ok %d / warn %d / bad %d @ %s'
        % (len(pts), cnt['ok'], cnt['warn'], cnt['bad'], meta['pull']))
    return True


def login():
    global OPENER
    jar = http.cookiejar.CookieJar()
    OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    try:
        op('/ares/a/login')
    except Exception:
        pass
    r = op('/ares/a/login', {'username': b64(USER), 'password': b64(PWD)})
    return 'true' in r


OPENER = urllib.request.build_opener()

if __name__ == '__main__':
    if not login():
        log('login failed')
        sys.exit(1)
    sys.exit(0 if pull_once() else 1)
