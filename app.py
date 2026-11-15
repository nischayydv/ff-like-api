from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson
import binascii
import aiohttp
import requests
import json
import like_pb2
import like_count_pb2
import uid_generator_pb2
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import random
import os

import jwt
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============ CONFIG ==============
API_KEY        = "VTX"
JWT_API_URL    = "https://all-in-one-jwt.vercel.app/token"

BD_CLIENT_URL  = "https://clientbp.ppmainecoonghj.com"
IND_CLIENT_URL = "https://clientbp.freefireind.in"
US_CLIENT_URL  = "https://client.us.freefiremobile.com"

OB55_USER_AGENT       = "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)"
OB55_X_GA_SV          = "1794159117"
OB55_X_UNITY_VERSION  = "2018.4.12f1"
OB55_RELEASE_VERSION  = "OB55"

KEY_LIMIT       = 90
TOKEN_CACHE_TTL = 7 * 60 * 60 
BASE_DIR        = os.path.dirname(os.path.abspath(__file__))

# ============== APP =============
app = Flask(__name__)

TOKEN_CACHE  = {}
tracker      = defaultdict(lambda: [0, time.time()])
liked_cache  = defaultdict(set)


# ============ HELPERS ============
def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, coro).result()


def get_today_midnight_timestamp():
    now = datetime.now()
    return datetime(now.year, now.month, now.day).timestamp()


def _account_file(server_name):
    """Map server_name -> JSON account file"""
    if server_name == "IND":
        return os.path.join(BASE_DIR, "accounts_ind.json")
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return os.path.join(BASE_DIR, "accounts_br.json")
    else:
        return os.path.join(BASE_DIR, "accounts_bd.json")


def load_accounts(server_name):
    filename = _account_file(server_name)

    if not os.path.exists(filename):
        fallback = os.path.join(BASE_DIR, "accounts_ind.json")
        print(f"⚠️ {os.path.basename(filename)} not found, trying accounts_ind.json")
        if not os.path.exists(fallback):
            print("❌ No account file found")
            return []
        filename = fallback

    accounts = []
    print(f"📂 Loading from: {os.path.basename(filename)} for server {server_name}")
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            print(f"❌ {os.path.basename(filename)} must be a JSON array []")
            return []

        for item in data:
            if not isinstance(item, dict):
                continue
            uid      = str(item.get("uid", "")).strip()
            password = str(item.get("password", "")).strip()
            if uid and password and uid.lower() != "uid" and password.lower() != "password":
                accounts.append({"uid": uid, "password": password})

    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in {os.path.basename(filename)}: {e}")
        return []
    except Exception as e:
        print(f"❌ Error loading accounts: {e}")
        return []

    print(f"✅ Total {len(accounts)} accounts loaded for {server_name}")
    return accounts


async def generate_jwt_token(uid, password):
    try:
        url = f"{JWT_API_URL}?uid={uid}&password={password}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=24) as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, dict):
                        return data.get('jwt_token') or data.get('token')
    except Exception as e:
        print(f"[JWT ERR] {e}")
    return None


async def get_valid_token(uid, password):
    if uid in TOKEN_CACHE:
        cached = TOKEN_CACHE[uid]
        remaining = (cached["expires_at"] - datetime.now(timezone.utc)).total_seconds()
        if remaining > 0:
            print(f"♻️ [CACHE HIT] UID {uid} — valid for {int(remaining)}s more")
            return cached["token"]
        else:
            print(f"⏰ [CACHE EXPIRED] UID {uid} — regenerating token")

    token = await generate_jwt_token(uid, password)
    if not token:
        return None

    TOKEN_CACHE[uid] = {
        "token": token,
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=TOKEN_CACHE_TTL),
    }
    print(f"🆕 [CACHE STORED] UID {uid} — cached for 7 hours")
    return token


def encrypt_message(plaintext):
    key = b'Yg&tc%DEuh6%Zc^8'
    iv  = b'6oyZDr22E3ychjM%'
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded = pad(plaintext, AES.block_size)
    return binascii.hexlify(cipher.encrypt(padded)).decode('utf-8')


def enc(uid):
    message = uid_generator_pb2.uid_generator()
    fields  = list(message.DESCRIPTOR.fields)
    setattr(message, fields[0].name, int(uid))
    if len(fields) > 1:
        setattr(message, fields[1].name, 1)
    return encrypt_message(message.SerializeToString())


def create_protobuf_message(user_id, region):
    message = like_pb2.like()
    message.uid = int(user_id)
    message.region = region
    return message.SerializeToString()


def decode_protobuf(binary):
    try:
        items = like_count_pb2.Info()
        items.ParseFromString(binary)
        return items
    except Exception:
        return None


# ============== API CALLS ==============
def _get_headers(token):
    return {
        'User-Agent': OB55_USER_AGENT,
        'Accept': "*/*",
        'Accept-Encoding': "deflate, gzip",
        'X-GA-SV': OB55_X_GA_SV,
        'Authorization': f"Bearer {token}",
        'X-GA': "v1 1",
        'ReleaseVersion': OB55_RELEASE_VERSION,
        'Content-Type': "application/x-www-form-urlencoded",
        'X-Unity-Version': OB55_X_UNITY_VERSION,
    }


def _client_url(server_name):
    if server_name == "IND":
        return IND_CLIENT_URL
    elif server_name in {"BR", "US", "SAC", "NA"}:
        return US_CLIENT_URL
    else:
        return BD_CLIENT_URL


def get_player_info(encrypted_uid, server_name, token):
    try:
        edata = bytes.fromhex(encrypted_uid)
        url = f"{_client_url(server_name)}/GetPlayerPersonalShow"
        response = requests.post(url, data=edata, headers=_get_headers(token),
                                 verify=False, timeout=10)
        return decode_protobuf(response.content)
    except Exception:
        return None


def _like_url(server_name):
    return f"{_client_url(server_name)}/LikeProfile"


async def send_like(encrypted_uid, token, url):
    try:
        edata = bytes.fromhex(encrypted_uid)
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=_get_headers(token),
                                    timeout=20) as response:
                return response.status
    except Exception:
        return 500


async def process_account(target_uid, encrypted_uid, account, url, semaphore):
    async with semaphore:
        token = await get_valid_token(account['uid'], account['password'])
        if not token:
            return 500, account['uid']

        status = await send_like(encrypted_uid, token, url)
        if status == 200:
            liked_cache[target_uid].add(account['uid'])
        return status, account['uid']


async def send_all_likes(target_uid, server_name, url):
    protobuf_message = create_protobuf_message(target_uid, server_name)
    encrypted_uid    = encrypt_message(protobuf_message)

    accounts = load_accounts(server_name)
    if not accounts:
        return {'success': 0, 'failed': 0, 'total': 0,
                'already_liked': 0, 'fresh_used': 0}

    already_liked = liked_cache.get(target_uid, set())
    fresh_accounts = [acc for acc in accounts if acc['uid'] not in already_liked]

    print(f"📊 Total: {len(accounts)} | Fresh: {len(fresh_accounts)} | Liked: {len(already_liked)}")

    if not fresh_accounts:
        return {'success': 0, 'failed': 0, 'total': len(accounts),
                'already_liked': len(already_liked), 'fresh_used': 0}

    random.shuffle(fresh_accounts)
    semaphore = asyncio.Semaphore(25)
    batch = fresh_accounts[:2000]
    tasks = [process_account(target_uid, encrypted_uid, acc, url, semaphore)
             for acc in batch]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    successful = failed = 0
    for r in results:
        if isinstance(r, tuple) and r[0] == 200:
            successful += 1
        else:
            failed += 1

    return {
        'success': successful,
        'failed': failed,
        'total': len(accounts),
        'already_liked': len(already_liked),
        'fresh_used': len(batch),
    }


# ============== ROUTES ================
@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "api": "RISHANT UID PASS LIKE API (OB55)",
        "token_cache_ttl": f"{TOKEN_CACHE_TTL}s (7 hours)",
        "channel": "t.me/RishantAPIs",
        "endpoints": {
            "/like": "?uid=UID&server_name=BD&key=VTX",
            "/reset-cache": "?key=VTX",
        },
    })


@app.route('/like', methods=['GET'])
def handle_requests():
    uid         = request.args.get("uid")
    server_name = request.args.get("server_name", "").upper()
    key         = request.args.get("key")
    client_ip   = request.remote_addr or "unknown"

    if key != API_KEY:
        return jsonify({"error": "Invalid or missing API key 🔑"}), 403
    if not uid or not server_name:
        return jsonify({"error": "UID and server_name are required"}), 400

    valid_servers = ["IND", "BR", "US", "SAC", "NA", "BD", "RU"]
    if server_name not in valid_servers:
        return jsonify({"error": f"Invalid server. Use: {valid_servers}"}), 400

    accounts = load_accounts(server_name) or load_accounts("IND")
    if not accounts:
        return jsonify({"error": f"No accounts found for server {server_name}"}), 500

    # ===== Daily limit =====
    today_midnight = get_today_midnight_timestamp()
    count, last_reset = tracker[client_ip]
    if last_reset < today_midnight:
        tracker[client_ip] = [0, time.time()]
        count = 0
    if count >= KEY_LIMIT:
        return jsonify({"error": "Daily limit reached",
                        "remains": f"(0/{KEY_LIMIT})"}), 429

    # ===== Get a check token =====
    check_token = None
    for account in accounts[:5]:
        check_token = run_async(get_valid_token(account['uid'], account['password']))
        if check_token:
            print(f"✅ Token generated with UID: {account['uid']}")
            break
    if not check_token:
        return jsonify({"error": "Token generation failed - no valid accounts"}), 500

    encrypted_uid = enc(uid)

    # ===== Before like =====
    before = get_player_info(encrypted_uid, server_name, check_token)
    if before is None:
        return jsonify({"error": "Invalid UID or server", "status": 0}), 200
    try:
        before_data = json.loads(MessageToJson(before))
        before_like = int(before_data['AccountInfo'].get('Likes', 0))
    except Exception:
        return jsonify({"error": "Data parsing failed", "status": 0}), 200

    # ===== Send likes =====
    like_url = _like_url(server_name)
    run_async(send_all_likes(uid, server_name, like_url))

    # ===== After like =====
    after = get_player_info(encrypted_uid, server_name, check_token)
    if after is None:
        return jsonify({"error": "Could not verify likes after command",
                        "status": 0}), 200

    try:
        after_data   = json.loads(MessageToJson(after))
        after_like   = int(after_data['AccountInfo']['Likes'])
        player_id    = int(after_data['AccountInfo']['UID'])
        player_name  = str(after_data['AccountInfo']['PlayerNickname'])

        like_given = after_like - before_like
        status     = 1 if like_given != 0 else 2

        if like_given > 0:
            tracker[client_ip][0] += 1
            count += 1

        remains = KEY_LIMIT - count

        return jsonify({
            "LikesGivenByAPI":   like_given,
            "LikesafterCommand": after_like,
            "LikesbeforeCommand": before_like,
            "PlayerNickname":    player_name,
            "UID":               player_id,
            "status":            status,
            "remains":           f"({remains}/{KEY_LIMIT})",
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": 0}), 500


@app.route('/reset-cache', methods=['GET'])
def reset_cache():
    if request.args.get("key") != API_KEY:
        return jsonify({"error": "Invalid key"}), 403
    liked_cache.clear()
    return jsonify({"message": "Cache cleared", "Channel": "t.me/RishantAPIs"})#RishantAPIs


# ============= ENTRY POINT ==============
if __name__ == '__main__':
    print("🚀 Server started - Smart Like System! (OB55 | 7H Token Cache)")
    print(f"⏱️  Token Cache TTL = {TOKEN_CACHE_TTL}s (7 hours)")
    print("📁 Account files:")
    print("   - accounts_ind.json (IND)")
    print("   - accounts_br.json  (BR/US/SAC/NA)")
    print("   - accounts_bd.json  (BD/RU)")
    app.run(host='0.0.0.0', port=5069, debug=False, use_reloader=False)