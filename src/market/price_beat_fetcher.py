"""
Price to Beat 获取器

通过 Playwright 常驻浏览器，每 15 分钟从 Polymarket 页面提取 price to beat。
"""

import asyncio
import re
from datetime import datetime

from src.config.settings import POLYMARKET_URL
from src.utils.logger import setup_logger

logger = setup_logger("price_beat")


class PriceBeatFetcher:
    """
    Price to Beat 获取器

    启动时创建常驻 headless 浏览器，每 15 分钟提取一次。
    """

    def __init__(self):
        self.browser = None
        self.page = None
        self._playwright = None

    async def start(self):
        """启动常驻浏览器"""
        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self.browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            self.page = await self.browser.new_page()
            # 设置合理的超时
            self.page.set_default_timeout(10000)
            logger.info("Playwright 浏览器已启动")
            return True

        except ImportError:
            logger.warning("Playwright 未安装，Price to Beat 功能不可用")
            logger.warning("安装: pip install playwright && playwright install chromium")
            return False
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}")
            return False

    async def fetch_price_to_beat(self, slug: str) -> float | None:
        """
        获取指定市场的 price to beat

        Args:
            slug: 市场 slug (btc-updown-15m-{timestamp})

        Returns:
            price to beat 价格，失败返回 None
        """
        if not self.page:
            logger.error("浏览器未启动")
            return None

        url = f"{POLYMARKET_URL}/event/{slug}"
        logger.info(f"获取 Price to Beat: {url}")

        try:
            # Navigate 到页面
            await self.page.goto(url, wait_until="domcontentloaded", timeout=10000)
            # 等待页面渲染
            await asyncio.sleep(3)

            # 尝试多种选择器提取 price to beat
            price = await self._extract_price()
            if price:
                logger.info(f"Price to Beat: ${price:,.2f}")
                return price

            # 备选: 搜索页面文本
            price = await self._extract_from_text()
            if price:
                logger.info(f"Price to Beat (文本): ${price:,.2f}")
                return price

            logger.warning(f"未找到 Price to Beat")
            return None

        except asyncio.TimeoutError:
            logger.warning("页面加载超时")
            return None
        except Exception as e:
            logger.error(f"获取失败: {e}")
            return None

    async def _extract_price(self) -> float | None:
        """通过 CSS 选择器提取"""
        selectors = [
            'text=Price to beat',
            'text="price to beat"',
            '[data-testid*="price"]',
            '.price-to-beat',
        ]

        for selector in selectors:
            try:
                element = self.page.locator(selector).first
                if await element.count() > 0:
                    text = await element.text_content()
                    price = self._parse_price(text)
                    if price > 0:
                        return price
            except Exception:
                continue

        return None

    async def _extract_from_text(self) -> float | None:
        """从页面全文搜索"""
        try:
            body = await self.page.locator("body").text_content()
            if not body:
                return None

            # 搜索 "$XX,XXX.XX" 格式，且在 "price to beat" 附近
            # 先找 "price to beat" 关键字
            lower_body = body.lower()
            idx = lower_body.find("price to beat")
            if idx < 0:
                idx = lower_body.find("price to")
            if idx < 0:
                return None

            # 从关键字位置向后搜索 200 字符
            nearby = body[idx:idx + 200]

            # 匹配价格格式
            matches = re.findall(r'\$?([\d,]+\.?\d*)', nearby)
            for match in matches:
                try:
                    price = float(match.replace(",", ""))
                    if 10000 < price < 200000:  # BTC 合理范围
                        return price
                except ValueError:
                    continue

            return None

        except Exception as e:
            logger.debug(f"文本提取失败: {e}")
            return None

    @staticmethod
    def _parse_price(text: str) -> float:
        """从文本解析价格"""
        if not text:
            return 0.0

        # 提取数字
        matches = re.findall(r'[\d,]+\.?\d*', text)
        for match in matches:
            try:
                price = float(match.replace(",", ""))
                if 10000 < price < 200000:
                    return price
            except ValueError:
                continue

        return 0.0

    async def stop(self):
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("浏览器已关闭")
