"""마진 대시보드 품목(Redash #7233) → '품목 선택' 탭용 암호화 목록(catalog.enc.json) 생성.

사용:
    python tools/build_catalog.py                 # redash_master 스킬로 #7233 최신 결과를 받아 생성
    python tools/build_catalog.py --input r.json  # 미리 받아 둔 결과(JSON: columns/rows)로 생성

- 비밀번호는 저장소 루트의 .catalog-password(git 제외)에서 읽는다. 바꾸려면 파일 내용을 고치고 다시 실행.
- 고객 정보(주문번호·한의원명·cid)는 담지 않고 품목 단위 단가·판매수량만 담는다.
- 금액은 #7233(VAT 제외)을 계산기 기준(VAT 포함)에 맞춰 ×1.1 한다.
"""
import argparse
import base64
import collections
import datetime
import hashlib
import json
import os
import re
import secrets
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY_ID = 7233
PBKDF2_ITER = 310000
VAT = 1.1

# 마진 대시보드(/margin)와 같은 공급처 유형 구분 — 피부미용 > 총판 > 비총판
SKIN = {'클래시스', '웰코멧코리아', '디에스이아이엔씨', 'DSE', '메디케네시스', '파이주식회사',
        '큐어멕', '큐어맥', '메딕콘', '더마팜', '은성', '라메디텍', '휴메디컬', '에이메디'}
TOTAL = {'다이나믹솔루션', '동아참메드', '메가메디칼', '메디뷰랩', '메디코아', '바이오시그널랩', '삼성메디슨',
         '오스테오시스', '오스테오시', '옴니씨앤에스', '웰코멧코리아', '은성', '클래시스', '팀엘리시움', '프리시젼바이오', '피아크'}

# 계산기 카테고리 자동 분류 (품목명 키워드, 위에서부터 먼저 맞는 것)
CATEGORY_RULES = [
    ('소모품', r'니들|카트리지|겔|팁\)|앰플|ml\*|소모품|필터|패드|전극|시약|튜브|스트립'),
    ('CO2 레이저/엔디야그', r'CO2|엔디야그|야그|레이저'),
    ('인젝터', r'주입기|인젝터|NEOJECT'),
    ('안면 진단기', r'피부진단|피부 진단|안면|3D'),
    ('바디 관리기기', r'바디|체형|메타쉐이프|퀀텀핏|체외충격파|ESWT'),
]


def won(x):
    """VAT 제외 → 포함(×1.1), 10원 단위 반올림"""
    return int(round(x * VAT, -1))


def sup_type(sup):
    if sup in SKIN:
        return '피부미용'
    if sup in TOTAL:
        return '총판'
    return '비총판'


def category_of(name):
    for cat, pat in CATEGORY_RULES:
        if re.search(pat, name, re.I):
            return cat
    return '기타 관리기기'


def fetch_from_redash():
    sys.path.insert(0, os.path.expanduser('~/.claude/skills/redash_master/scripts'))
    from redash_client import redash_request
    q = redash_request('get_query', path_params={'id': QUERY_ID})
    r = redash_request('get_query_results', path_params={'id': q['latest_query_data_id']})
    qr = r['query_result']
    return {'retrieved_at': qr.get('retrieved_at'),
            'columns': [c['name'] for c in qr['data']['columns']],
            'rows': qr['data']['rows']}


def build_items(rows):
    rows = [r for r in rows if (r.get('수량') or 0) > 0 and (r.get('매출금액') or 0) > 0]
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r.get('공급업체') or '(미지정)', r.get('품목명') or '')].append(r)

    items, used_ids = [], set()
    for (sup, name), rs in groups.items():
        if not name:
            continue
        last_date = max(r['주문일'] for r in rs)
        last = [r for r in rs if r['주문일'] == last_date]
        last_qty = sum(r['수량'] for r in last)
        all_qty = sum(r['수량'] for r in rs)
        code = (last[-1].get('품목번호') or '').strip()
        iid = 'mg_' + (code or hashlib.md5(f'{sup}|{name}'.encode()).hexdigest()[:10])
        while iid in used_ids:
            iid += '_'
        used_ids.add(iid)
        items.append({
            'id': iid,
            'name': name,
            'sup': sup,
            'type': sup_type(sup),
            'cat': category_of(name),
            'price': won(sum(r['매출금액'] for r in last) / last_qty),
            'cost': won(sum(r['공급가'] or 0 for r in last) / last_qty),
            'avgPrice': won(sum(r['매출금액'] for r in rs) / all_qty),
            'avgCost': won(sum(r['공급가'] or 0 for r in rs) / all_qty),
            'qty': int(all_qty),
            'orders': len({r.get('주문번호') for r in rs}),
            'last': str(last_date)[:10],
        })
    items.sort(key=lambda it: -it['avgPrice'] * it['qty'])
    return items


def encrypt(payload, password):
    salt, iv = secrets.token_bytes(16), secrets.token_bytes(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITER).derive(password.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {'v': 1, 'kdf': 'PBKDF2-SHA256', 'iter': PBKDF2_ITER, 'salt': b64(salt), 'iv': b64(iv), 'ct': b64(ct)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', help='#7233 결과 JSON (columns/rows). 없으면 Redash에서 받음')
    ap.add_argument('--out', default=os.path.join(ROOT, 'catalog.enc.json'))
    args = ap.parse_args()

    pw_path = os.path.join(ROOT, '.catalog-password')
    if not os.path.exists(pw_path):
        sys.exit('.catalog-password 파일이 없습니다 (저장소 루트에 비밀번호 한 줄)')
    password = open(pw_path, encoding='utf-8').read().strip()

    data = json.load(open(args.input, encoding='utf-8')) if args.input else fetch_from_redash()
    items = build_items(data['rows'])
    payload = {
        'v': 1,
        'source': f'Redash #{QUERY_ID} MD 스쿼드 매출 내역 (2025~, sbi)',
        'retrieved_at': data.get('retrieved_at'),
        'built_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'items': items,
    }
    json.dump(encrypt(payload, password), open(args.out, 'w', encoding='utf-8'))
    print(f'{len(items)}개 품목 → {args.out}')


if __name__ == '__main__':
    main()
