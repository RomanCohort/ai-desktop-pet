"""
OC Desktop Pet - 模块化重构入口
原 oc.py 中的 DesktopPet 类保留 UI/交互逻辑，
业务逻辑（API、记忆、情绪、经济等）委托给各子模块。
"""
import sys
import os

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 导入原始 DesktopPet（保留完整 UI 逻辑）
# 后续逐步将 oc.py 中的方法替换为模块化实现
from oc import DesktopPet

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--create-kb-wrappers', action='store_true')
    parser.add_argument('--write-kb-reg', action='store_true')
    parser.add_argument('--install-kb', action='store_true')
    parser.add_argument('--uninstall-kb', action='store_true')
    parser.add_argument('--python-path', help='Optional python.exe absolute path')
    args, unknown = parser.parse_known_args()

    pet = DesktopPet()
    pet.run()
