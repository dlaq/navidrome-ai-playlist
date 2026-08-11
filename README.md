# 🎵 Navidrome AI 智能歌单生成器

通过 AI 搜索各大音乐平台，自动匹配你的 Navidrome 曲库，一键生成带封面的歌单。

## ✨ 功能特性

### 🔍 多平台音乐搜索
- **酷我音乐** - 海量曲库
- **网易云音乐** - 精准推荐
- **QQ音乐** - 正版资源
- **酷狗音乐** - 热门金曲

### 🧠 智能匹配
- 搜索结果自动与你的 Navidrome 曲库匹配
- 支持跨平台标准化匹配：全角/半角符号、大小写、歌手分隔符、合作艺人、Live、Remix、伴奏、Acoustic 等写法不一致时仍可匹配
- 使用标题相似度 + 歌手集合评分，避免仅凭歌名把同名歌曲匹配错
- 显示匹配率和未找到的歌曲列表

### 💾 曲库索引
- 使用 Navidrome `search3` 分页快速扫描全库，不再逐个遍历艺术家和专辑
- 曲库元数据持久化到 `/data/navidrome_library.json`
- 已有缓存时先使用旧索引，后台刷新完成前不会把曲库误判为空

### 🎨 自动生成封面
- 根据歌单主题智能生成精美封面
- 22 种主题风格可选：
  - 音乐风格：民谣、摇滚、电子、古典、流行、爵士、嘻哈、R&B、轻音乐、国风
  - 语言：日语、韩语、粤语
  - 场景：深夜、早晨、运动、伤感、治愈、浪漫
  - 年代：80后、90后
- 创建前可预览封面效果

### ✅ 一键创建歌单
- 匹配到的歌曲直接创建为 Navidrome 歌单
- 连接 Songloft Subsonic 插件时，创建歌单后通过 Songloft 原生 API 单独上传封面
- 支持创建前勾选/取消歌曲

### 📋 歌单链接导入
- 粘贴歌单链接，自动获取歌曲并匹配曲库
- 支持网易云音乐、QQ音乐、酷我、酷狗的歌单URL

### 🔒 安全
- 密码保护的 Web UI
- 24 小时会话有效期

## 🚀 快速部署（Docker）

### 第一步：克隆项目并构建镜像

SSH 登录你的 NAS，执行：

```bash
git clone https://github.com/yueyoue/navidrome-ai-playlist.git
cd navidrome-ai-playlist
docker build -t navidrome-ai-playlist .
```

等待构建完成（首次约 1-2 分钟），看到 `FINISHED` 表示成功。

### 第二步：启动容器

```bash
docker run -d \
  --name navidrome-ai-playlist \
  -p 8899:8899 \
  -v /你的路径/navidrome-ai-playlist-data:/data \
  -e NAVIDROME_URL=http://你的NAS局域网IP:4533/ \
  -e NAVIDROME_USER=你的Navidrome用户名 \
  -e NAVIDROME_PASS=你的Navidrome密码 \
  -e LOGIN_PASSWORD=你想设置的Web访问密码 \
  --restart unless-stopped \
  navidrome-ai-playlist
```

> ⚠️ 把上面的中文替换成你自己的实际值。`NAVIDROME_URL` 建议用局域网 IP，速度更快。

### 第三步：访问

浏览器打开 `http://你的NAS地址:8899`，输入 `LOGIN_PASSWORD` 即可登录。

## ⚙️ 环境变量说明

| 环境变量 | 说明 | 示例 |
|---------|------|------|
| `NAVIDROME_URL` | Navidrome 服务器地址（末尾加 `/`） | `http://192.168.1.100:4533/` |
| `NAVIDROME_USER` | Navidrome 用户名 | `admin` |
| `NAVIDROME_PASS` | Navidrome 密码 | `your_password` |
| `LOGIN_PASSWORD` | Web UI 访问密码 | `your_web_password` |
| `SONGLOFT_API_URL` | Songloft 原生 API 地址（可选，通常可从 Subsonic 插件地址自动推断） | `http://192.168.1.100:58091/api/v1` |
| `SONGLOFT_ACCESS_TOKEN` | Songloft 原生 API Access Token（可选；不设置时使用账号自动登录） | — |
| `PORT` | 服务端口（可选，默认 8899） | `8899` |
| `LIBRARY_CACHE_PATH` | 曲库缓存文件路径（可选） | `/data/navidrome_library.json` |
| `LIBRARY_CACHE_TTL` | 自动刷新间隔秒数（可选，默认 3600） | `3600` |

## 🎯 使用方法

### 方式一：搜索主题创建
1. 浏览器打开 `http://你的NAS地址:8899`
2. 输入访问密码登录
3. 在搜索框输入歌单主题，例如：
   - 「民谣歌单」
   - 「80后经典」
   - 「周杰伦精选」
   - 「深夜emo」
4. 选择搜索来源（酷我/网易/QQ/酷狗）
5. 点击「AI 搜索匹配」，等待 10-30 秒
6. 查看匹配结果，可切换封面风格并预览
7. 勾选想要的歌曲，点击「创建歌单」

### 方式二：歌单链接导入
1. 在网易云/QQ音乐/酷我/酷狗中找到喜欢的歌单
2. 复制歌单链接
3. 粘贴到页面的「粘贴歌单链接」输入框
4. 点击「导入歌单」
5. 系统自动获取歌曲并匹配你的曲库
6. 勾选想要的歌曲，点击「创建歌单」

创建完成后打开 Navidrome 即可看到新歌单（含封面）！

## 🔧 常用命令

```bash
# 查看容器状态
docker ps | grep navidrome-ai

# 查看日志
docker logs -f navidrome-ai-playlist

# 重启服务
docker restart navidrome-ai-playlist

# 停止并删除容器
docker stop navidrome-ai-playlist
docker rm navidrome-ai-playlist

# 更新代码后重新部署
cd ~/navidrome-ai-playlist
git pull
docker build -t navidrome-ai-playlist .
docker stop navidrome-ai-playlist
docker rm navidrome-ai-playlist
# 然后重新执行上面的 docker run 命令
```

## 📁 项目结构

```
navidrome-ai-playlist/
├── app.py                 # FastAPI 主应用
├── config.py              # 配置文件
├── navidrome_client.py    # Navidrome Subsonic API 客户端
├── cover_generator.py     # 封面生成器（22种主题配色）
├── searchers/
│   └── __init__.py        # 多平台搜索引擎
├── templates/
│   ├── login.html         # 登录页
│   └── app.html           # 主操作页
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 🛠️ 技术栈

- **后端**：Python + FastAPI
- **前端**：HTML + Tailwind CSS + JavaScript
- **搜索引擎**：酷我、网易云、QQ音乐、酷狗
- **音乐服务器**：Navidrome (Subsonic API)
- **封面生成**：Pillow
- **部署**：Docker（基础镜像使用华为云国内源，国内可正常拉取）

## ⚠️ 注意事项

- 首次启动会后台加载歌曲库（曲库越大加载越慢）
- 首次扫描完成前，匹配接口会提示“曲库正在首次加载”，不会返回误导性的 0 首匹配
- 创建歌单时先无封面创建，再按每批最多 50 首添加歌曲；失败批次会重试并二分隔离坏歌曲
- 使用 Songloft 时，`NAVIDROME_URL` 填写 Subsonic 插件地址（例如 `http://服务器:58091/api/v1/jsplugin/subsonic/`），程序会自动推断原生 API 地址
- 使用 `docker run` 时建议挂载 `/data`；否则删除容器后曲库缓存也会随容器删除
- 搜索结果取决于各音乐平台 API 的可用性
- 匹配结果会显示匹配分数；低置信度结果会标记为“模糊”供人工确认
- 不要在 `docker-compose.yml` 或命令行中泄露你的密码配置

## 📄 License

MIT
