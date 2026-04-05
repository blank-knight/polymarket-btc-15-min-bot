"""
实盘部署配置

包含钱包初始化、链上交互、部署脚本。
"""

import os
from src.config.settings import (
    POLYGON_PRIVATE_KEY,
    POLYGON_RPC_URL,
    API_KEY,
    API_SECRET,
    API_PASSPHRASE,
    TRADING_MODE,
)
from src.utils.logger import setup_logger

logger = setup_logger("deployment")


def check_live_config() -> tuple[bool, str]:
    """检查实盘配置是否完整"""
    issues = []

    if not POLYGON_PRIVATE_KEY:
        issues.append("POLYGON_PRIVATE_KEY 未设置")
    if not API_KEY:
        issues.append("CLOB_API_KEY 未设置")
    if not API_SECRET:
        issues.append("CLOB_API_SECRET 未设置")
    if not API_PASSPHRASE:
        issues.append("CLOB_API_PASSPHRASE 未设置")

    if issues:
        return False, "缺少配置: " + "; ".join(issues)
    return True, "配置完整"


def get_clob_client():
    """
    获取 Polymarket CLOB 客户端

    需要安装 py_clob_client
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds

        client = ClobClient(
            POLYGON_RPC_URL,
            chain_id=137,  # Polygon Mainnet
            key=POLYGON_PRIVATE_KEY,
            api_creds=ApiCreds(
                api_key=API_KEY,
                api_secret=API_SECRET,
                api_passphrase=API_PASSPHRASE,
            ),
        )
        logger.info("CLOB 客户端初始化成功")
        return client

    except ImportError:
        logger.error("py_clob_client 未安装: pip install py_clob_client")
        return None
    except Exception as e:
        logger.error(f"CLOB 客户端初始化失败: {e}")
        return None


def print_deployment_guide():
    """打印部署指南"""
    print("""
🚀 BTC 15M Bot 部署指南
========================

1. 安装依赖:
   cd polymarket-btc-15min-bot
   source venv/bin/activate
   pip install playwright
   playwright install chromium

2. 配置钱包:
   cp .env.example .env
   # 编辑 .env，填入:
   #   POLYGON_PRIVATE_KEY=你的私钥
   #   CLOB_API_KEY=你的API Key
   #   CLOB_API_SECRET=你的API Secret
   #   CLOB_API_PASSPHRASE=你的API Passphrase
   #   TRADING_MODE=SIMULATION  (先模拟!)

3. 测试运行:
   python main.py              # 单次扫描
   python tests/test_step12_backtest.py  # 回测

4. 24/7 运行 (推荐 systemd):
   sudo tee /etc/systemd/system/btc-15m-bot.service << 'EOF'
   [Unit]
   Description=BTC 15M Trading Bot
   After=network.target

   [Service]
   Type=simple
   User=zwt
   WorkingDirectory=/home/zwt/clawd/polymarket-btc-15min-bot
   ExecStart=/home/zwt/clawd/polymarket-btc-15min-bot/venv/bin/python main.py --run
   Restart=always
   RestartSec=30

   [Install]
   WantedBy=multi-user.target
   EOF

   sudo systemctl enable btc-15m-bot
   sudo systemctl start btc-15m-bot

5. 查看日志:
   tail -f data/bot.log

6. 切换实盘:
   # 修改 .env:
   TRADING_MODE=LIVE
   # 重启:
   sudo systemctl restart btc-15m-bot
""")
