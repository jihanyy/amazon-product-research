import csv
import json
import os
import random
import re
import signal
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# 类目配置
CATEGORIES = {
    "厨房餐厅": "https://www.amazon.com/gp/bestsellers/kitchen",
    "家居厨房": "https://www.amazon.com/gp/bestsellers/home-garden",
    "玩具游戏": "https://www.amazon.com/gp/bestsellers/toys-and-games",
    "汽配用品": "https://www.amazon.com/gp/bestsellers/automotive",
    "手机配件": "https://www.amazon.com/gp/bestsellers/wireless",
    "手工制品": "https://www.amazon.com/gp/bestsellers/handmade",
    "美术手工": "https://www.amazon.com/gp/bestsellers/arts-crafts",
}


class AmazonBestsellerScraper:
    def __init__(self):
        self.csv_file = "amazon.csv"
        self.seller_cache_file = "seller_country_cache.json"
        self.data = []
        self.seller_cache = {}
        self.interrupted = False
        self.driver = None
        self._written_count = 0  # 已写入CSV的行数

        self._load_cache()
        signal.signal(signal.SIGINT, self._signal_handler)

    def _load_cache(self):
        """加载卖家国家缓存"""
        if os.path.exists(self.seller_cache_file):
            try:
                with open(self.seller_cache_file, 'r', encoding='utf-8') as f:
                    self.seller_cache = json.load(f)
                print(f"已加载卖家缓存: {len(self.seller_cache)} 条记录")
            except Exception:
                self.seller_cache = {}

    def _signal_handler(self, sig, frame):
        """处理 Ctrl+C 中断，保存已爬取的数据"""
        print("\n检测到中断信号，正在保存已爬取的数据...")
        self.interrupted = True
        self._save_data()
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None
        except Exception:
            pass
        sys.exit(0)

    def _save_data(self):
        """保存数据到 CSV（追加模式）和 卖家缓存到 JSON"""
        new_data = self.data[self._written_count:]
        if new_data:
            file_exists = os.path.exists(self.csv_file) and os.path.getsize(self.csv_file) > 0
            with open(self.csv_file, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["类目名", "商品标题", "价格", "卖家名称", "卖家国家"]
                )
                if not file_exists:
                    writer.writeheader()
                writer.writerows(new_data)
            self._written_count = len(self.data)
            print(f"  已追加 {len(new_data)} 条数据到 {self.csv_file}")

        with open(self.seller_cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.seller_cache, f, ensure_ascii=False, indent=2)

    def _dedup_csv(self):
        """去重CSV：同一商品标题+卖家名称保留最后一条（最新）"""
        if not os.path.exists(self.csv_file):
            return
        rows = []
        with open(self.csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        if not rows:
            return

        seen = {}
        for i, row in enumerate(rows):
            key = (row.get("商品标题", ""), row.get("卖家名称", ""))
            seen[key] = i  # 保留最后一次的索引

        deduped = [rows[i] for i in sorted(seen.values())]
        removed = len(rows) - len(deduped)

        if removed > 0:
            with open(self.csv_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["类目名", "商品标题", "价格", "卖家名称", "卖家国家"]
                )
                writer.writeheader()
                writer.writerows(deduped)
            print(f"  CSV去重：移除 {removed} 条重复数据，最终 {len(deduped)} 条")

    def _init_driver(self):
        """初始化 Chrome 浏览器"""
        # 关闭上一个可能残留的浏览器
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

        chrome_options = Options()
        # chrome_options.add_argument("--headless")  # 如需无界面可取消注释
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--lang=en-US")
        # 不等待图片/资源加载完，页面DOM就绪即可
        chrome_options.page_load_strategy = 'eager'
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=chrome_options)
        # CDP: 让浏览器认为窗口始终聚焦，避免后台节流
        try:
            self.driver.execute_cdp_cmd('Emulation.setFocusEmulationEnabled', {'enabled': True})
        except Exception:
            pass
        print("浏览器已启动")

    def _set_us_location(self):
        """设置配送地址为美国（邮编 10001），失败自动重试"""
        for attempt in range(1, 4):
            try:
                print(f"正在设置美国配送地址... (第{attempt}次)")
                short_wait = WebDriverWait(self.driver, 8)
                loc_elem = short_wait.until(
                    EC.element_to_be_clickable((By.ID, "glow-ingress-line1"))
                )
                loc_elem.click()
                time.sleep(0.8)

                zip_input = short_wait.until(
                    EC.presence_of_element_located((By.ID, "GLUXZipUpdateInput"))
                )
                zip_input.clear()
                zip_input.send_keys("10001")

                apply_btn = self.driver.find_element(By.ID, "GLUXZipUpdate")
                apply_btn.click()
                time.sleep(1)

                try:
                    confirm_btn = short_wait.until(
                        EC.element_to_be_clickable((
                            By.XPATH,
                            "//input[@aria-labelledby='GLUXConfirmTitle']"
                        ))
                    )
                    confirm_btn.click()
                    time.sleep(0.5)
                except Exception:
                    pass

                print("美国地址设置完成")
                return
            except Exception:
                if attempt < 3:
                    print(f"  设置失败，刷新页面后重试...")
                    self.driver.refresh()
                    time.sleep(2)
                else:
                    print("设置地址失败，已达最大重试次数，继续运行...")

    def _scroll_to_load_items(self, expected_count=50):
        """
        逐步下滑页面，每次滚动一段距离，让商品逐步加载，
        直到加载出 expected_count 个商品或无法继续加载为止。
        """
        scroll_distance = 1000
        last_count = 0
        stale_count = 0

        for i in range(80):
            if self.interrupted:
                break

            items = self.driver.find_elements(
                By.XPATH, "//div[starts-with(@id, 'gridItemRoot')]"
            )
            current_count = len(items)

            if current_count >= expected_count:
                print(f"  已加载 {current_count} 个商品，满足要求")
                break

            if current_count == last_count:
                stale_count += 1
                if stale_count >= 6:
                    print(f"  滚动停止，共加载 {current_count} 个商品")
                    break
            else:
                stale_count = 0

            last_count = current_count

            # 模拟窗口活跃：派发事件，避免后台节流导致懒加载不触发
            self.driver.execute_script("window.dispatchEvent(new Event('focus'));")
            self.driver.execute_script("window.dispatchEvent(new MouseEvent('mousemove', {clientX: 500, clientY: 300}));")
            self.driver.execute_script("window.dispatchEvent(new Event('scroll'));")

            # 逐步滚动，而不是直接滚到底
            current_y = self.driver.execute_script("return window.scrollY")
            self.driver.execute_script(f"window.scrollTo(0, {current_y + scroll_distance});")
            time.sleep(1.2)

        # 返回页面顶部
        self.driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

    def _get_list_products(self):
        """从列表页提取商品标题、价格、链接"""
        items = self.driver.find_elements(
            By.XPATH, "//div[starts-with(@id, 'gridItemRoot')]"
        )
        products = []
        for item in items:
            try:
                # 标题
                title_elem = item.find_element(
                    By.XPATH, ".//a[contains(@class, 'a-link-normal')]//span"
                )
                title = title_elem.text.strip()

                # 链接
                link_elem = item.find_element(
                    By.XPATH, ".//a[contains(@href, '/dp/')]"
                )
                href = link_elem.get_attribute("href")
                if href:
                    href = href.split("?")[0].split("/ref=")[0]

                # 价格（多种选择器）
                price = ""
                price_selectors = [
                    ".//span[contains(@class, 'a-price')]//span[@class='a-offscreen']",
                    ".//span[contains(@class, 'a-price-whole')]",
                    ".//span[contains(@class, 'a-price') and contains(@class, 'a-text-price')]",
                    ".//span[contains(@class, 'a-color-price')]",
                ]
                for ps in price_selectors:
                    try:
                        pe = item.find_element(By.XPATH, ps)
                        price = pe.text.strip()
                        if price:
                            break
                    except Exception:
                        continue

                if title and href and href not in [p["url"] for p in products]:
                    products.append({"title": title, "price": price, "url": href})
            except Exception:
                continue
        return products

    def _get_seller_info(self, url):
        """进入商品详情页，只获取卖家名称和卖家国家。"""
        self.driver.get(url)
        # 页面可见就继续，不用等完全加载
        time.sleep(random.uniform(1, 2))

        seller_name = ""
        seller_country = ""

        # 1. 判断亚马逊自营（Shipper / Seller）
        try:
            shipper = self.driver.find_element(
                By.XPATH,
                "//*[contains(text(), 'Shipper / Seller')]/following-sibling::*"
            )
            if "Amazon" in shipper.text:
                return "Amazon", ""
        except Exception:
            pass

        # 2. 优先从 Buy Box 区域判断卖家
        buybox_seller = None
        try:
            buybox = self.driver.find_element(By.ID, "buybox")
            buybox_text = buybox.text
            if "Shipper / Seller" in buybox_text and "Amazon" in buybox_text:
                return "Amazon", ""
            # 精确查找 Sold by 后面的卖家链接
            sold_by_links = buybox.find_elements(
                By.XPATH, ".//span[contains(text(), 'Sold by')]/following-sibling::a"
            )
            if sold_by_links:
                seller_name = sold_by_links[0].text.strip()
                if "Amazon" in seller_name:
                    return "Amazon", ""
                if seller_name in self.seller_cache and self.seller_cache[seller_name]:
                    return seller_name, self.seller_cache[seller_name]
                buybox_seller = seller_name
            elif "Sold by" in buybox_text:
                sold_match = re.search(r"Sold by\s+(.+)", buybox_text)
                if sold_match:
                    seller_name = sold_match.group(1).strip().split('\n')[0]
                    if "Amazon" in seller_name:
                        return "Amazon", ""
                    if seller_name in self.seller_cache and self.seller_cache[seller_name]:
                        return seller_name, self.seller_cache[seller_name]
                    buybox_seller = seller_name
        except Exception:
            pass

        # 3. 下滑到底部，找卖家链接
        self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)

        sold_by_elem = None
        try:
            sold_by_elem = self.driver.find_element(
                By.XPATH,
                "//span[contains(text(), 'Sold by')]/following-sibling::a | "
                "//a[contains(@id, 'merchant') or "
                "contains(@href, 'merchant') or "
                "contains(@href, 'seller-profile') or "
                "contains(@href, 'seller=')]"
            )
            seller_name = sold_by_elem.text.strip()

            if "Amazon" in seller_name:
                return "Amazon", ""

            if seller_name in self.seller_cache and self.seller_cache[seller_name]:
                return seller_name, self.seller_cache[seller_name]

        except Exception:
            # 用 BuyBox 中记住的卖家名
            if buybox_seller:
                seller_name = buybox_seller
                # 尝试找卖家链接
                try:
                    sold_by_elem = self.driver.find_element(
                        By.XPATH,
                        "//a[contains(@href, 'seller-profile') or contains(@href, 'seller=')]"
                    )
                except Exception:
                    pass
            else:
                return "Amazon", ""

        # 4. 点击卖家名，进入卖家信息页获取国家（JS点击避免被拦截）
        if sold_by_elem:
            self.driver.execute_script("arguments[0].click();", sold_by_elem)
            time.sleep(random.uniform(2, 4))
            seller_country = self._extract_country()

        # 5. 只存非空国家
        if seller_country:
            self.seller_cache[seller_name] = seller_country

        # 返回
        try:
            self.driver.back()
            time.sleep(0.5)
        except Exception:
            pass

        return seller_name, seller_country

    def _extract_country(self):
        """从卖家信息页提取国家，优先通过 DOM 结构获取。"""
        country = ""
        try:
            time.sleep(0.8)

            # 方法1：找到 Business Address 所在父节点，取其后所有行
            try:
                addr_parent = self.driver.find_element(
                    By.XPATH,
                    "//div[contains(text(), 'Business Address')]/parent::*"
                )
                rows = addr_parent.find_elements(
                    By.XPATH, ".//div[contains(@class, 'a-row')]"
                )
                addr_started = False
                addr_lines = []
                for row in rows:
                    text = row.text.strip()
                    if not text:
                        continue
                    if "Business Address" in text:
                        addr_started = True
                        continue
                    if addr_started:
                        # 遇到下一个主要字段时结束
                        if any(k in text for k in ["Business Name", "Shipping", "Contact", "Customer"]):
                            break
                        addr_lines.append(text)
                if addr_lines:
                    country = addr_lines[-1]
            except Exception:
                pass

            # 方法2：文本解析兜底
            if not country:
                body = self.driver.find_element(By.TAG_NAME, "body").text
                if "Business Address" in body:
                    addr_text = body.split("Business Address", 1)[1]
                    for sep in ["Business Name", "Shipping", "Contact", "Customer", "Policies", "Ratings", "Returns"]:
                        if sep in addr_text:
                            addr_text = addr_text.split(sep, 1)[0]
                            break
                    lines = [l.strip() for l in addr_text.split('\n') if l.strip()]
                    if lines:
                        country = lines[-1]

            # 方法3：正则兜底
            if not country:
                body = self.driver.find_element(By.TAG_NAME, "body").text
                match = re.search(
                    r'Business Address[\s:]*\n(.*?)(?=\n\n|\n[A-Z][a-z]+:|\Z)',
                    body, re.DOTALL
                )
                if match:
                    addr = match.group(1).strip()
                    lines = [l.strip() for l in addr.split('\n') if l.strip()]
                    if lines:
                        country = lines[-1]

            # 国家归一化
            if country:
                cu = country.upper()
                if cu in ['US', 'USA', 'UNITED STATES']:
                    country = 'US'
                elif cu in ['CN', 'CHINA', 'P.R.C', "PEOPLE'S REPUBLIC OF CHINA"]:
                    country = 'CN'

        except Exception:
            pass

        return country

    def _remove_category_from_csv(self, category_name):
        """爬取前删除CSV中该类的旧数据，实现替换而非追加"""
        if not os.path.exists(self.csv_file):
            return
        rows = []
        with open(self.csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        kept = [r for r in rows if r.get("类目名") != category_name]
        removed = len(rows) - len(kept)
        if removed > 0:
            with open(self.csv_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=["类目名", "商品标题", "价格", "卖家名称", "卖家国家"]
                )
                writer.writeheader()
                writer.writerows(kept)
            print(f"  已移除 {category_name} 旧数据 {removed} 条")

    def scrape(self, category_name, url):
        """爬取一个类目的两页畅销榜数据"""
        print(f"\n>>> 开始爬取类目: {category_name} ({url})")

        # 移除CSV中该类目旧数据，并清理内存中该类目数据
        self._remove_category_from_csv(category_name)
        self.data = [d for d in self.data if d["类目名"] != category_name]
        self._written_count = len(self.data)

        self._init_driver()

        try:
            self.driver.get(url)
            time.sleep(3)

            # 设置美国地址，确保是美国榜单
            self._set_us_location()

            # 重新打开榜单页
            self.driver.get(url)
            time.sleep(3)

            for page in range(1, 3):
                if self.interrupted:
                    break

                print(f"\n[{category_name} - 第 {page} 页]")

                if page > 1:
                    # 先回到列表页（上一页的首页），才能看到翻页按钮
                    self.driver.get(url)
                    time.sleep(2)
                    self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                    time.sleep(0.8)

                    next_btn = None
                    next_xpaths = [
                        "//a[contains(@class, 's-pagination-next')]",
                        "//a[contains(text(), 'Next')]",
                        "//a[contains(@aria-label, 'Next')]",
                        "//li[@class='a-last']//a",
                        "//ul[contains(@class, 'a-pagination')]//li[contains(@class, 'a-last')]//a",
                    ]
                    for xp in next_xpaths:
                        try:
                            next_btn = self.driver.find_element(By.XPATH, xp)
                            if next_btn and 'a-disabled' not in next_btn.get_attribute('class'):
                                break
                            next_btn = None
                        except Exception:
                            continue

                    if not next_btn:
                        print(f"  第 {page} 页 Next 按钮未找到或已禁用，停止翻页")
                        break

                    self.driver.execute_script("arguments[0].scrollIntoView();", next_btn)
                    time.sleep(0.5)
                    next_btn.click()
                    time.sleep(3)
                    print(f"  已翻至第 {page} 页")

                # 下滑直到加载 50 条数据
                self._scroll_to_load_items(50)

                # 提取列表页商品信息
                products = self._get_list_products()
                print(f"本页共找到 {len(products)} 个商品")

                for idx, p in enumerate(products):
                    if self.interrupted:
                        break

                    try:
                        seller, country = self._get_seller_info(p["url"])

                        self.data.append({
                            "类目名": category_name,
                            "商品标题": p["title"] or "未知",
                            "价格": p["price"],
                            "卖家名称": seller or "未知",
                            "卖家国家": country
                        })

                        short_title = (p["title"][:50] + "...") if p["title"] and len(p["title"]) > 50 else (p["title"] or "未知")
                        country_display = "" if seller == "Amazon" else (country or "N/A")
                        print(f"  [{page}-{idx + 1:02d}/{len(products):02d}] {short_title:<55} | 价格: {p['price'] or 'N/A':<12} | 卖家: {seller or 'N/A':<20} | 国家: {country_display}")

                    except Exception as e:
                        print(f"  [{page}-{idx + 1:02d}/{len(products):02d}] 处理失败: {e}")
                        continue

                    # 每 50 条静默保存一次
                    if len(self.data) % 50 == 0:
                        self._save_data()

                # 每页结束保存一次
                self._save_data()

        except Exception as e:
            print(f"爬取过程中出现错误: {e}")

        finally:
            self._save_data()
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass
            self.driver = None

        # 统计本类目数据
        cat_data = [d for d in self.data if d["类目名"] == category_name]
        amazon_count = sum(1 for d in cat_data if d["卖家名称"] == "Amazon")
        country_count = {}
        for d in cat_data:
            if d["卖家名称"] == "Amazon":
                continue
            c = d["卖家国家"] or "未知"
            country_count[c] = country_count.get(c, 0) + 1

        print(f"\n[{category_name}] 爬取结束！共收集 {len(cat_data)} 条数据")
        print(f"  其中 Amazon 自营: {amazon_count} 条")
        for cntry, cnt in sorted(country_count.items(), key=lambda x: x[1], reverse=True):
            print(f"  {cntry}: {cnt} 条")

        return cat_data


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Amazon 畅销榜爬虫")
    parser.add_argument(
        "categories",
        nargs="*",
        help="要爬取的类目名称，支持多个。不指定则爬取所有类目。"
    )
    args = parser.parse_args()

    # 确定要爬取的类目
    if args.categories:
        targets = []
        for c in args.categories:
            if c in CATEGORIES:
                targets.append((c, CATEGORIES[c]))
            else:
                print(f"未知类目: '{c}'，可用类目: {', '.join(CATEGORIES.keys())}")
    else:
        targets = list(CATEGORIES.items())

    if not targets:
        print("没有可爬取的类目，退出。")
        sys.exit(0)

    print(f"将爬取 {len(targets)} 个类目: {', '.join(t[0] for t in targets)}")

    scraper = AmazonBestsellerScraper()
    all_data = []

    for category_name, url in targets:
        if scraper.interrupted:
            print(f"已中断，跳过剩余类目。")
            break
        try:
            data = scraper.scrape(category_name, url)
            all_data.extend(data)
        except Exception as e:
            print(f"类目 '{category_name}' 爬取出错: {e}")

    # CSV去重
    scraper._dedup_csv()

    # 最终统计
    total = len(all_data)
    amazon_count = sum(1 for d in all_data if d["卖家名称"] == "Amazon")
    country_count = {}
    cat_stats = {}
    for d in all_data:
        c = d["卖家国家"] or "未知"
        if d["卖家名称"] != "Amazon":
            country_count[c] = country_count.get(c, 0) + 1
        cat = d["类目名"]
        cat_stats[cat] = cat_stats.get(cat, 0) + 1

    print(f"\n全部爬取结束！共收集 {total} 条数据")
    for cat, cnt in cat_stats.items():
        print(f"  {cat}: {cnt} 条")
    print(f"  其中 Amazon 自营: {amazon_count} 条")
    for cntry, cnt in sorted(country_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cntry}: {cnt} 条")
    print(f"CSV 文件: {scraper.csv_file}")
    print(f"卖家缓存: {scraper.seller_cache_file}")
