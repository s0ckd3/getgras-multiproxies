import json
import aiohttp
import asyncio

async def login_account(payload):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.getgrass.io/login", json=payload) as response:
                data = await response.json()
                # Kiểm tra dữ liệu trả về
                if not data.get("result") or not data["result"].get("data"):
                    return None
                return data["result"]["data"]
    except Exception as error:
        # In ra lỗi (nếu cần) và trả về None
        return None

async def check_point(token):
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Authorization": token
    }
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get("https://api.getgrass.io/dailyEarnings?input=%7B%22limit%22:1%7D") as response:
                json_data = await response.json()
                data_array = None
                if (json_data.get("result") and 
                    json_data["result"].get("data") and 
                    json_data["result"]["data"].get("data")):
                    data_array = json_data["result"]["data"]["data"]
                if not data_array or not isinstance(data_array, list) or len(data_array) == 0:
                    return json_data.get("error", {}).get("message", "No earnings data")
                # Sắp xếp theo earningDate giảm dần (mới nhất đầu tiên)
                data_array.sort(key=lambda x: x.get("earningDate"), reverse=True)
                newest = data_array[0]
                return {
                    "totalUptime": newest.get("totalUptime"),
                    "totalPoints": newest.get("totalPoints")
                }
    except Exception as error:
        return None
