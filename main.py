import os
import json
import asyncio
import sys
import shutil
import aiohttp
import logging
from auth import login_account, check_point
from app import start_app

logging.basicConfig(level=logging.INFO)

async def delay(ms):
    await asyncio.sleep(ms/1000)

def chunk_array(arr, size):
    return [arr[i:i+size] for i in range(0, len(arr), size)]

async def main():
    # Đường dẫn đến thư mục data (nằm cùng cấp với main.py)
    data_dir = os.path.join(os.getcwd(), 'data')
    config_path = os.path.join(data_dir, 'setting.json')
    with open(config_path, 'r', encoding='utf8') as f:
        config = json.load(f)
    
    version = config.get("version")
    devicetype = config.get("devicetype")
    extension_id = config.get("extension_id")
    batch_size = config.get("number_of_turn")
    time_delay = config.get("time_delay")
    proxy_path = config.get("proxy_path")
    proxy_api_key = config.get("proxy_api_key")
    
    logging.info("Configuration: %s", {
        "version": version,
        "devicetype": devicetype,
        "extension_id": extension_id,
        "batchSize": batch_size,
        "time_delay": time_delay,
        "proxy_api_key": proxy_api_key,
        "proxy_path": proxy_path,
        "Create": "2movn.com"
    })
    
    # Đăng nhập tài khoản
    login_payload = {
        "username": config.get("username"),
        "password": config.get("password")
    }
    user_data = await login_account(login_payload)
    if not user_data:
        logging.error("Login Account failed. Turning off app in 10s")
        await delay(10000)
        sys.exit(1)
    
    # Cập nhật thông tin người dùng vào file settingStorage.json
    original_storage_path = os.path.join(data_dir, 'settingStorage.json')
    original_storage = {}
    if os.path.exists(original_storage_path):
        try:
            with open(original_storage_path, 'r', encoding='utf8') as f:
                original_storage = json.load(f)
        except Exception as e:
            logging.error("Error reading settingStorage.json: %s", e)
    original_storage["wynd:user_id"] = user_data.get("userId")
    original_storage["accessToken"] = user_data.get("accessToken")
    original_storage["refreshToken"] = user_data.get("refreshToken")
    original_storage["email"] = user_data.get("email")
    original_storage["username"] = "2mo.vn"
    with open(original_storage_path, 'w', encoding='utf8') as f:
        json.dump(original_storage, f, indent=2)
    
    # Lấy danh sách proxy từ API hoặc từ file
    proxies = []
    if proxy_api_key and proxy_api_key.strip() != "":
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://ipviet.store/orders/api/get-proxy?api_key={proxy_api_key}"
                async with session.get(url) as response:
                    data = await response.json()
                    if not data.get("status"):
                        logging.error("No Proxy with API")
                    proxies_data = data.get("proxies", [])
                    proxies = [
                        f"{item.get('type','').lower()}://{item.get('username')}:{item.get('password')}@{item.get('host')}:{item.get('port')}"
                        for item in proxies_data
                    ]
        except Exception as e:
            logging.error("Error fetching proxies: %s", e)
    else:
        try:
            file_path = proxy_path if os.path.isabs(proxy_path) else os.path.join(data_dir, proxy_path)
            with open(file_path, 'r', encoding='utf8') as f:
                content = f.read()
            proxies = [line.strip() for line in content.splitlines() if line.strip()]
        except Exception as e:
            logging.error("Error reading proxies from file: %s", e)
    
    if not proxies:
        logging.error("Proxies not found. Turning off app in 10s")
        await delay(10000)
        sys.exit(1)
    
    # Lấy điểm tích lũy (point)
    point_result = await check_point(user_data.get("accessToken"))
    if isinstance(point_result, dict):
        logging.info(f"Total point: {point_result.get('totalPoints')} | Total Uptime: {point_result.get('totalUptime')}")
    else:
        logging.info("CheckPoint error or no data: %s", point_result)
    logging.info(f"Found {len(proxies)} proxy(s).")
    
    # Chia danh sách proxy thành các nhóm (batch)
    proxy_batches = chunk_array(proxies, batch_size)
    
    overall_index = 0
    for batch_index, batch in enumerate(proxy_batches):
        logging.info(f"Processing batch {batch_index+1} of {len(proxy_batches)}")
        for proxy in batch:
            instance_storage_path = os.path.join(data_dir, f"setting_{overall_index}.json")
            # Nếu file instance chưa tồn tại, nhân bản từ file gốc
            if not os.path.exists(instance_storage_path):
                shutil.copyfile(original_storage_path, instance_storage_path)
            # Gọi hàm start_app với tùy chọn: proxy và storageFileOverride cho instance này
            asyncio.create_task(start_app({
                "proxy": proxy,
                "storageFileOverride": instance_storage_path
            }))
            overall_index += 1
        logging.info(f"Batch {batch_index+1} completed. Waiting for {time_delay} ms before next batch.")
        await delay(time_delay)
    
    # Tạo một trình chạy riêng cho checkPoint: cứ 10 phút chạy checkPoint và in ra thông báo
    async def run_check_point_periodically():
        token = user_data.get("accessToken")
        result = await check_point(token)
        if isinstance(result, dict):
            logging.info(f"(2movn Check-point) Total Uptime: {result.get('totalUptime')} | Total Points: {result.get('totalPoints')}")
        else:
            logging.info("(2movn) Error or no data: %s", result)
        while True:
            await asyncio.sleep(10 * 60)
            result = await check_point(token)
            if isinstance(result, dict):
                logging.info(f"(2movn Check-point) Total Uptime: {result.get('totalUptime')} | Total Points: {result.get('totalPoints')}")
            else:
                logging.info("(2movn) Error or no data: %s", result)
    asyncio.create_task(run_check_point_periodically())

if __name__ == "__main__":
    asyncio.run(main())
