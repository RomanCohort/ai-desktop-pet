# AI 桌宠 (AI Desktop Pet) 🐾

> 基于 AI 的桌面宠物 — 情感系统 × 记忆系统 × TTS 语音 × Bilibili VTuber，一只能说会道还能陪你学习的智能桌宠。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-blue?logo=windows)
[![AI](https://img.shields.io/badge/AI-DeepSeek%20%7C%20GPT%20%7C%20Claude-FF6B00?logo=openai)
[![Bilibili](https://img.shields.io/badge/VTuber-Bilibili-FF69B4?logo=bilibili)](https://live.bilibili.com)

## ✨ 功能特性

### 🤖 AI 智能交互
- **多模型对话** — 支持 DeepSeek / GPT / Claude 等主流大模型 API
- **长期记忆检索** — 基于 RAG 的上下文注入，记得你说过的每一句话
- **情感与心情系统** — 桌宠会根据对话内容和互动频率动态调整心情与好感度

### 🗣️ 语音能力
- **本地 TTS 朗读** — 无需云端 API，pyttsx3 离线语音引擎
- **口型动画** — 角色说话时自动开合嘴动画，逼真自然
- **可调参数** — 语速、音量、音色自由调节

### 🎮 互动功能
- **好感度经济** — 喂食、互动增加好感度，解锁专属内容
- **背包 / 商城系统** — 道具收集与消费循环
- **猜拳 / 掷骰子** — 桌宠陪你玩的小游戏
- **随机事件** — 每天都有新鲜感

### 🖥️ 系统监控
- **CPU / 内存实时监控** — 桌面宠物实时显示电脑状态
- **网络延迟检测** — 监控网络状态
- **剪贴板互动** — 检测剪贴板内容，触发互动
- **游戏进程检测** — 识别游戏运行，自动静音提醒

### 📅 日程与陪伴
- **整点报时** — 准点播报时间
- **昼夜作息** — 23:00–07:00 自动切换睡眠模式
- **专注时钟** — 番茄钟 + 监督模式，学习效率翻倍
- **日程提醒** — 设置提醒，桌宠准时提醒

### 🔬 学术增强（iGEM 专项）
- **组会记录** — 记录并整理会议内容
- **生信工作流** — 质粒设计、PCR 引物等咨询
- **任务看板** — iGEM 项目任务管理

### 🎙️ VTuber 模式
- **Bilibili 直播** — 一键切换为 VTuber 模式
- **弹幕互动** — 实时读取并回复弹幕

## 📁 项目结构

```
ai-desktop-pet/
├── main.py                      # 桌面宠物入口
├── vtuber_main.py               # VTuber 模式入口
├── oc.py                        # 主逻辑（桌面宠物）
├── config_vtuber.yaml           # VTuber 配置
├── make_assets.py               # 表情图生成工具
├── requirements.txt             # Python 依赖
├── settings.json               # 用户配置（API Key 等）
└── oc_desktop_pet/
    ├── animation/               # 动画与精灵加载
    ├── chat/                    # 对话系统（API / 记忆 / Prompt）
    ├── emotions/                # 情感 / 好感度 / 经济系统
    ├── features/                # 功能模块（iGEM / 会议 / 文档）
    ├── monitors/                # 系统监控
    ├── perception/              # 飞书集成
    ├── persistence/             # 配置持久化
    ├── utils/                   # 工具函数
    └── vtuber/                  # Bilibili VTuber
```

## 🚀 快速开始

### 安装

```bash
git clone https://github.com/RomanCohort/ai-desktop-pet.git
cd ai-desktop-pet
pip install -r requirements.txt
```

或直接双击 `install.bat`（Windows）。

### 配置

编辑 `settings.json`：

```json
{
  "api": {
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "api_key": "YOUR_API_KEY"
  },
  "tts": {
    "rate": 150,
    "volume": 1.0,
    "voice": 0
  },
  "pet": {
    "scale": 1.0,
    "position": "bottom-right"
  }
}
```

### 运行

```bash
# 桌面宠物模式
python main.py

# VTuber 模式
python vtuber_main.py
```

## 🎨 资源准备

桌宠需要以下图片资源（放在项目根目录）：

| 文件名 | 说明 | 必需 |
|--------|------|------|
| `normal1.png` | 静止表情 | ✅ |
| `normal2.png` | 张嘴表情 | ❌（可选） |
| `oc.ico` | 程序图标 | ❌ |
| `source.png` | 原始素材 | 用于生成表情图 |

运行 `make_assets.bat` 可从 `source.png` 自动生成所需表情图。

## 🎯 适用场景

| 场景 | 说明 |
|------|------|
| 🏠 桌面陪伴 | 有个不会打扰你工作的可爱桌宠随时陪伴 |
| 📚 学习监督 | 专注时钟 + 桌宠监督，学习效率可视化 |
| 🧬 iGEM 竞赛 | 内置 IGEM 助手，支持组会记录和任务管理 |
| 🎙️ 直播互动 | VTuber 模式，在 Bilibili 直播答疑 |
| 🔧 系统监控 | 桌面实时显示 CPU / 内存 / 网络状态 |

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| GUI | tkinter（Python 标准库） |
| AI 对话 | DeepSeek / GPT / Claude API |
| TTS | pyttsx3（本地离线） |
| 知识检索 | LangChain（RAG） |
| 直播连接 | Bilibili WebSocket API |
| 配置管理 | PyYAML + JSON |

## 🙏 致谢

本项目灵感来源于 ZerolanLiveRobot 系列项目，以及 fake-neuro 社区的创意贡献。

## 📝 License

MIT License &copy; 2024 [RomanCohort](https://github.com/RomanCohort)

---

*如果这个桌宠给你带来了快乐，请 ⭐ Star 支持一下！*
