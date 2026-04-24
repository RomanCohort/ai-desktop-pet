# AI 桌宠 (AI Desktop Pet)

基于 AI 的桌面宠物，具备情感系统、记忆系统、TTS 语音、Bilibili VTuber 功能。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows-blue)

## 功能特性

### 核心交互
- 角色说话时自动开合嘴动画
- 空闲状态随机眨眼
- 打字机式文本显示
- 可调节桌宠大小（60%~180%）

### AI 能力
- 多模型 API 对话（支持 DeepSeek 等）
- 长期记忆检索与上下文注入
- 情感与心情系统
- 好感度与金币经济循环

### 系统监控
- CPU / 内存实时监控
- 网络延迟检测
- 剪贴板互动
- 游戏进程检测与静音
- 媒体 / 音乐嗅探

### 工具功能
- 背包 / 商城 / 喂食
- 日程提醒与随机事件
- 观察日记生成
- 文件整理
- 猜拳 / 掷骰子小游戏
- iGEM 助手（组会记录 / 生信工作流 / 任务看板）

### 语音
- 本地 TTS 语音朗读（pyttsx3）
- 可调语速、音量、音色

### 日程与陪伴
- 整点报时
- 昼夜作息（23:00-07:00 睡眠模式）
- 专注时钟与监督

## 项目结构

```
├── main.py                          # 程序入口
├── oc.py                            # 主逻辑
├── vtuber_main.py                   # VTuber 模式入口
├── config_vtuber.yaml               # VTuber 配置
├── make_assets.py                   # 表情图生成工具
├── oc_desktop_pet/                  # 核心包
│   ├── animation/                   # 动画与精灵加载
│   ├── chat/                        # 对话系统 (API/记忆/Prompt)
│   ├── emotions/                    # 情感/好感度/经济
│   ├── features/                    # 功能模块 (iGEM/会议/文档等)
│   ├── monitors/                    # 系统监控
│   ├── perception/                  # 飞书集成
│   ├── persistence/                 # 配置持久化
│   ├── utils/                       # 工具函数
│   └── vtuber/                      # Bilibili VTuber
└── requirements.txt                 # Python 依赖
```

## 安装

```bash
git clone https://github.com/RomanCohort/ai-desktop-pet.git
cd ai-desktop-pet
pip install -r requirements.txt
```

或双击 `install.bat`（Windows）。

## 运行

```bash
python main.py
```

## 资源准备

- `normal1.png` — 静止表情
- `normal2.png` — 张嘴表情（可选）
- `oc.ico` — 程序图标

使用 `make_assets.bat` 可从 `source.png` 自动生成。

## 配置

编辑 `settings.json` 配置 API Key 和各项参数。

## 许可证

MIT License


## Related Projects

| Project | Description |
|---------|-------------|
| [paper-search-tool](https://github.com/RomanCohort/paper-search-tool) | AI 论文搜索与整理工具 |
| [ai-desktop-pet](https://github.com/RomanCohort/ai-desktop-pet) | AI 桌面宠物 |
| [web-crawler-v2](https://github.com/RomanCohort/web-crawler-v2) | 网站爬取器 |
| [berlin-tank-commander](https://github.com/RomanCohort/berlin-tank-commander) | 柏林车长文字冒险 |
| [bioease](https://github.com/RomanCohort/bioease) | 生物信息学分析 |
| [IGEM-sama](https://github.com/RomanCohort/IGEM-sama) | IGEM AI 虚拟主播 |
| [ppt-agent](https://github.com/RomanCohort/ppt-agent) | PPT 草稿生成器 |

