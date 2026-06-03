import os
import re
import time
import random
import argparse
import requests

from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# 配置
# =========================

RANDOM_SLEEP = False
SESSION_REUSE = True   # 是否启用 Session 复用
SESSION_MAX_TASK = 100 # 每个 Session 复用的最大请求次数，之后自动切换新 Session

PAGE_SIZE = 100
MAX_WORKERS = 4 # 建议不要>5
SAVE_DIR = "." # will concat: SAVE_DIR/order_id/

HEADERS = {
    "Referer": "https://www.yipai360.com/",
    "Origin": "https://www.yipai360.com",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36"
    )
}


# =========================
# Session 复用管理
# =========================

class SessionPool:
    """线程安全的 Session 复用池，每个 Session 最多复用 SESSION_MAX_TASK 次后自动刷新。"""

    def __init__(self, enabled=True, max_tasks=100):
        self.enabled = enabled
        self.max_tasks = max_tasks
        self._lock = __import__("threading").Lock()
        self._session = None
        self._counter = 0
        self._refresh()

    def _refresh(self):
        self._session = requests.Session()
        self._session.headers.update(HEADERS)
        self._counter = 0

    def get(self):
        if not self.enabled:
            return None
        with self._lock:
            self._counter += 1
            if self._counter > self.max_tasks:
                self._refresh()
            return self._session

    def request(self, method, url, **kwargs):
        """
        使用复用 Session 发起请求。
        当 SESSION_REUSE=False 时，退化为普通 requests.get/post 调用。
        """
        if self.enabled:
            session = self.get()
            return session.request(method, url, **kwargs)
        else:
            return requests.request(method, url, headers=HEADERS, **kwargs)


session_pool = SessionPool(enabled=SESSION_REUSE, max_tasks=SESSION_MAX_TASK)


def _random_sleep_if_needed():
    if RANDOM_SLEEP:
        time.sleep(random.uniform(0.5, 1.5))


# =========================
# 提取 orderId
# =========================

def extract_order_id(url):
    m = re.search(r'orderId=([0-9]+)', url)

    if not m:
        raise ValueError("无法从 URL 中提取 orderId")

    return m.group(1)


# =========================
# 获取分页数据
# =========================

def fetch_page(order_id, page, tag_id=""):

    api = (
        f"https://www.yipai360.com/api/v1/yipai/order/"
        f"{order_id}/audience/photos"
    )

    params = {
        "tagId": tag_id,
        "pwd": "",
        "sortType": "desc",
        "page": page,
        "pageSize": PAGE_SIZE
    }

    _random_sleep_if_needed()

    r = session_pool.request(
        "GET",
        api,
        params=params,
        timeout=30
    )

    r.raise_for_status()

    return r.json()["data"]


# =========================
# 下载单张图片
# =========================

def download_photo(photo, save_dir):

    img = photo["img"]

    # 原图 URL（不要 watermark）
    url = img["primary"] + img["path"]

    # 原始文件名
    filename = photo.get("fname")

    if not filename:
        ext = photo.get("ext", ".jpg")
        filename = f'{photo["photoId"]}{ext}'

    filepath = os.path.join(save_dir, filename)

    # 已存在则跳过
    if os.path.exists(filepath):
        return "skip"

    try:

        _random_sleep_if_needed()

        r = session_pool.request(
            "GET",
            url,
            stream=True,
            timeout=60
        )

        r.raise_for_status()

        with open(filepath, "wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)

        return "ok"

    except Exception as e:
        return f"error: {e}"


# =========================
# 主函数
# =========================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "url",
        help="photolive 页面 URL"
    )

    parser.add_argument(
        "--test",
        type=int,
        default=None,
        help="测试模式，只下载前 N 张照片"
    )

    parser.add_argument(
    "--tag-id",
    type=str,
    default="",
    help="只下载指定 tagId 的照片"
)

    args = parser.parse_args()

    photolive_url = args.url

    order_id = extract_order_id(photolive_url)

    print(f"\norderId: {order_id}")

    os.makedirs(os.path.join(SAVE_DIR, order_id), exist_ok=True)

    # 获取第一页
    first_page = fetch_page(order_id, 1, args.tag_id)

    pagination = first_page["pagination"]

    total = pagination["count"]
    total_pages = pagination["totalPage"]

    print(f"总照片数: {total}")
    print(f"总页数: {total_pages}")

    # 收集所有照片
    photos = []

    print("\n获取分页数据...")

    for page in tqdm(range(1, total_pages + 1)):

        data = fetch_page(order_id, page, args.tag_id)

        photos.extend(data["photos"])

        # test 模式提前停止收集
        if args.test and len(photos) >= args.test:
            photos = photos[:args.test]
            break

    print(f"\n准备下载 {len(photos)} 张照片...\n")

    success = 0
    skipped = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

        futures = [
            executor.submit(download_photo, photo, os.path.join(SAVE_DIR, order_id))
            for photo in photos
        ]

        for future in tqdm(as_completed(futures), total=len(futures)):

            result = future.result()

            if result == "ok":
                success += 1

            elif result == "skip":
                skipped += 1

            else:
                failed += 1
                print(result)

    print("\n下载完成")
    print(f"成功: {success}")
    print(f"跳过: {skipped}")
    print(f"失败: {failed}")


if __name__ == "__main__":
    main()