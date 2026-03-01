# 商业帝国 - Telegram 公司经营游戏机器人

基于 Telegram 的多人公司经营模拟游戏。玩家通过 **科研 → 研发树 → 产品 → 利润** 的路径经营虚拟公司，包含股东系统、每日结算、地产投资、分红、AI研发评审、交易所等玩法。

## 技术栈

- **语言**: Python 3.11+
- **Bot框架**: aiogram 3.x（异步Telegram框架）
- **数据库**: PostgreSQL + asyncpg（通过SQLAlchemy 2.0 async ORM，支持高并发）
- **缓存**: Redis（热数据/分布式锁/排行榜/冷却计时/管理员认证/道具Buff）
- **定时任务**: APScheduler（每日结算 + 自动备份）
- **配置**: pydantic-settings（类型安全）
- **部署**: Docker Compose（推荐）/ 本地 Python 运行

## 快速开始（Docker，推荐）

```bash
# 1. 克隆项目
git clone https://github.com/keys-cherish/my_company.git
cd my_company

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 BOT_TOKEN、管理员ID/超管ID、群组和话题限制等配置

# 3. 启动（bot + postgres + redis）
docker compose up -d --build

# 4. 查看状态
docker compose ps
docker compose logs -f bot
```

## 快速开始（本地 Python）

```bash
# 1. 克隆项目
git clone https://github.com/keys-cherish/my_company.git
cd my_company

# 2. 安装依赖（需要先安装 uv）
uv sync

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 BOT_TOKEN、数据库连接、管理员ID等配置

# 4. 确保 PostgreSQL 和 Redis 已启动
# PostgreSQL: 创建数据库和用户
# Redis: redis-server &

# 5. 启动机器人（自动建表）
uv run python bot.py
```

## 核心玩法

| 系统 | 说明 |
|------|------|
| 公司系统 | 8种公司类型（科技/金融/传媒/制造/地产/生物/游戏/咨询），各有专属Buff |
| 股东系统 | 投资获取股份，老板最低持股保护，按股份分红 |
| 科研系统 | 10级研发树，前置解锁，完成后解锁新产品 |
| 产品系统 | 创建/升级/下架产品，每日产生收入 |
| AI研发 | 提交产品方案 → AI评分 → 永久提升产品收入(1-100%) |
| 路演系统 | 消耗金币路演，随机获得资源/声望/积分 |
| 合作系统 | 公司间合作提供营收加成 |
| 地产系统 | 购买地产获取稳定被动收入 |
| 广告系统 | 4档广告方案，临时提升营收 |
| 交易所 | 金币/额度/积分互换，道具商城，黑市特惠 |
| 员工系统 | 招聘/裁员，薪资/社保成本 |
| 税务系统 | 每日营收纳税 |
| 随机事件 | 员工离职/退休/市场波动/PR危机等20+事件 |
| 积分系统 | 多途径获取积分，可兑换金币 |
| 每日结算 | 自动计算收入/分红/生成日报 |

## 命令

| 命令 | 范围 | 说明 |
|------|------|------|
| `/start` | 私聊+群组 | 注册/查看个人面板 |
| `/company` | 私聊+群组 | 查看/管理公司 |
| `/admin <密钥>` | 私聊 | 管理员认证（需配置ID+密钥） |
| 其他操作 | 私聊+群组 | 通过Inline键盘菜单操作 |

## 配置

所有游戏参数均可通过 `.env` 文件或管理员面板实时调整，详见 `.env.example`。

常用配置项：
- `BOT_TOKEN`：机器人 token
- `DATABASE_URL`：数据库连接（生产建议 PostgreSQL）
- `REDIS_URL`：Redis 连接
- `ADMIN_TG_IDS`：管理员 TG ID 列表（逗号分隔）
- `SUPER_ADMIN_TG_ID` / `SUPER_ADMIN_TG_IDS`：超级管理员（支持单个或多个）
- `ALLOWED_CHAT_IDS`：允许的群组 ID（逗号分隔）
- `ALLOWED_CHAT_USERNAMES`：允许的群组用户名（可选，逗号分隔）
- `ALLOWED_TOPIC_THREAD_IDS`：允许的话题 ID 列表（可选，逗号分隔）
- `ALLOWED_TOPIC_THREAD_ID`：单话题兼容配置（旧版，保留可用）
- `BACKUP_ENABLED`：是否启用自动备份
- `BACKUP_INTERVAL_MINUTES`：自动备份间隔（分钟）
- `BACKUP_KEEP_FILES`：本地保留的备份文件数量
- `BACKUP_NOTIFY_SUPER_ADMIN`：备份结果是否私聊通知超管

备份说明：
- 备份文件写入项目根目录，文件名形如 `my_company_backup_YYYYMMDDTHHMMSSZ.json.gz`
- 该备份是 `my_company` 项目独立文件，不使用 `dice_bot` 的 `backup.db`

## 项目结构

```
my_company/
├── bot.py              # 入口
├── config.py           # 配置
├── pyproject.toml      # uv项目配置
├── db/                 # 数据库模型
├── cache/              # Redis缓存
├── services/           # 业务逻辑（15个服务）
├── handlers/           # Telegram交互处理（13个）
├── keyboards/          # Inline键盘布局
├── scheduler/          # 定时任务
├── game_data/          # 游戏静态数据(JSON)
└── utils/              # 工具函数
```
