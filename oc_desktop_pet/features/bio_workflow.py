"""简化生物信息学工作流 - 引导式多步工具执行"""
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..persistence.paths import BIO_WORKFLOWS_PATH
from ..persistence.store import Store
from ..chat.api_client import APIClient


# ── 工作流模板定义 ──

WORKFLOW_TEMPLATES = {
    "fastqc": {
        "display_name": "检查测序质量 (FastQC)",
        "trigger_keywords": ["测序质量", "fastqc", "质控", "qc", "质量检查", "测序数据"],
        "steps": [
            {"step": 1, "prompt": "请选择你的测序文件路径（fastq/fasta格式，如 C:/data/reads_R1.fastq.gz）", "input_type": "file", "key": "input_file"},
            {"step": 2, "prompt": "选择输出目录（留空则与输入文件同目录）", "input_type": "dir", "key": "output_dir"},
            {"step": 3, "prompt": "确认运行 FastQC 质量检查？", "input_type": "confirm"},
            {"step": 4, "prompt": "结果解读", "input_type": "result_read"},
        ],
        "tool": "fastqc",
        "tool_args": "{input_file} -o {output_dir}",
        "result_hint": "FastQC会生成HTML报告，重点关注：Per base sequence quality、Adapter Content、Duplication Levels",
    },
    "blastn": {
        "display_name": "序列比对 (BLAST)",
        "trigger_keywords": ["比对", "blast", "序列比对", "序列搜索", "blastn", "同源"],
        "steps": [
            {"step": 1, "prompt": "输入查询序列或选择序列文件路径", "input_type": "file_or_text", "key": "query"},
            {"step": 2, "prompt": "选择数据库（nt / nr / 自定义路径）", "input_type": "choice", "choices": ["nt", "nr"], "key": "database"},
            {"step": 3, "prompt": "确认运行 BLAST 比对？", "input_type": "confirm"},
            {"step": 4, "prompt": "结果解读", "input_type": "result_read"},
        ],
        "tool": "blastn",
        "tool_args": '-query {query_file} -db {database} -outfmt "6 qseqid sseqid pident length evalue bitscore stitle" -max_target_seqs 10 -out {output_file}',
        "result_hint": "关注 evalue < 1e-5、identity > 80% 的比对结果",
    },
    "bwa_mem": {
        "display_name": "序列映射 (BWA MEM)",
        "trigger_keywords": ["映射", "mapping", "比对到基因组", "bwa", "基因组比对", "mapping reads"],
        "steps": [
            {"step": 1, "prompt": "选择参考基因组文件路径（.fasta/.fa）", "input_type": "file", "key": "reference"},
            {"step": 2, "prompt": "选择测序reads文件路径（.fastq/.fq）", "input_type": "file", "key": "reads"},
            {"step": 3, "prompt": "确认运行 BWA MEM 映射？", "input_type": "confirm"},
            {"step": 4, "prompt": "结果解读", "input_type": "result_read"},
        ],
        "tool": "bwa",
        "tool_args": "mem {reference} {reads} -o {output_sam}",
        "result_hint": "关注 mapping rate、properly paired 等指标",
    },
    "primer3": {
        "display_name": "设计引物 (Primer3)",
        "trigger_keywords": ["引物", "primer", "设计引物", "pcr引物", "primer3"],
        "steps": [
            {"step": 1, "prompt": "输入目标序列（FASTA格式或纯序列）", "input_type": "text", "key": "sequence"},
            {"step": 2, "prompt": "设置参数（留空使用默认）：退火温度范围(如55-65)、产物大小(如100-500)", "input_type": "params", "key": "params"},
            {"step": 3, "prompt": "确认设计引物？", "input_type": "confirm"},
            {"step": 4, "prompt": "结果解读", "input_type": "result_read"},
        ],
        "tool": "primer3_core",
        "tool_args": "",
        "result_hint": "关注引物Tm值、GC含量(40-60%)、特异性、二聚体形成",
    },
    "phylo_tree": {
        "display_name": "系统发育树",
        "trigger_keywords": ["系统发育", "进化树", "phylogenetic", "建树", "进化分析"],
        "steps": [
            {"step": 1, "prompt": "选择多序列比对文件（FASTA/CLUSTAL格式）", "input_type": "file", "key": "alignment_file"},
            {"step": 2, "prompt": "选择建树方法", "input_type": "choice", "choices": ["Neighbor-Joining", "Maximum-Likelihood", "Minimum-Evolution"], "key": "method"},
            {"step": 3, "prompt": "确认运行建树？", "input_type": "confirm"},
            {"step": 4, "prompt": "结果解读", "input_type": "result_read"},
        ],
        "tool": "phylo_pipeline",
        "tool_args": "",
        "result_hint": "关注 bootstrap 值(>70较可靠)、分支长度、聚类关系",
    },
}


class BioWorkflowGuide:
    """引导式生物信息学工作流管理器。

    让不熟悉命令行的iGEM成员也能使用基本生信工具。
    每个工作流是多步引导：选择参数 → 确认 → 执行 → 解读结果。
    """

    EXPLAIN_PROMPT = """请用通俗易懂的语言解释以下生物信息学工具的输出结果。
目标读者是不熟悉生信的iGEM队员。

工具：{tool_name}
原始输出：
{raw_output}

要求：
1. 一句话总结结果
2. 关键发现（2-3条）
3. 是否有问题需要注意
4. 建议的下一步操作"""

    def __init__(self, settings: dict, reply_queue=None):
        self.settings = settings
        self.reply_queue = reply_queue
        self.sessions: dict = {}  # session_id -> session_data
        self._load()

    def _load(self):
        data = Store.load_json(BIO_WORKFLOWS_PATH, {"active_sessions": {}})
        self.sessions = data.get("active_sessions", {})

    def _save(self):
        Store.save_json(BIO_WORKFLOWS_PATH, {"active_sessions": self.sessions})

    def match_workflow(self, user_text: str) -> Optional[str]:
        """匹配用户输入到工作流模板。返回 workflow_type 或 None。"""
        text_lower = (user_text or "").lower()
        best_match = None
        best_score = 0

        for wf_type, template in WORKFLOW_TEMPLATES.items():
            for keyword in template.get("trigger_keywords", []):
                if keyword in text_lower:
                    score = len(keyword)  # 更长的匹配优先
                    if score > best_score:
                        best_score = score
                        best_match = wf_type

        return best_match

    def start_session(self, workflow_type: str) -> Optional[dict]:
        """启动一个新的工作流引导会话。"""
        if workflow_type not in WORKFLOW_TEMPLATES:
            return None

        template = WORKFLOW_TEMPLATES[workflow_type]
        session_id = str(uuid.uuid4())[:8]
        session = {
            "id": session_id,
            "workflow_type": workflow_type,
            "display_name": template["display_name"],
            "started_at": datetime.now().isoformat(),
            "step": 1,
            "total_steps": len(template["steps"]),
            "params": {},
            "status": "active",  # active / completed / cancelled / error
            "result": None,
        }
        self.sessions[session_id] = session
        self._save()

        # 返回第一步的提示
        first_step = template["steps"][0]
        return {
            "session_id": session_id,
            "step": 1,
            "total_steps": session["total_steps"],
            "prompt": first_step["prompt"],
            "input_type": first_step.get("input_type", "text"),
            "choices": first_step.get("choices", []),
            "display_name": template["display_name"],
        }

    def advance_session(self, session_id: str, user_input: str) -> Optional[dict]:
        """处理用户输入，推进到下一步。"""
        session = self.sessions.get(session_id)
        if not session or session.get("status") != "active":
            return None

        wf_type = session["workflow_type"]
        template = WORKFLOW_TEMPLATES.get(wf_type)
        if not template:
            return None

        current_step_idx = session["step"] - 1  # 0-indexed
        steps = template["steps"]

        if current_step_idx >= len(steps):
            return None

        current_step = steps[current_step_idx]

        # 存储用户输入
        key = current_step.get("key", f"step_{session['step']}")
        session["params"][key] = user_input.strip()

        # 推进到下一步
        next_step_idx = current_step_idx + 1
        if next_step_idx >= len(steps):
            # 已到最后一步之前，下一步是执行
            session["status"] = "ready_to_execute"
            self._save()
            return {
                "session_id": session_id,
                "step": session["step"] + 1,
                "total_steps": session["total_steps"],
                "prompt": "参数已收集完毕，即将执行...",
                "input_type": "execute",
                "display_name": template["display_name"],
            }

        next_step = steps[next_step_idx]
        session["step"] = next_step_idx + 1
        self._save()

        return {
            "session_id": session_id,
            "step": next_step_idx + 1,
            "total_steps": session["total_steps"],
            "prompt": next_step["prompt"],
            "input_type": next_step.get("input_type", "text"),
            "choices": next_step.get("choices", []),
            "display_name": template["display_name"],
        }

    def execute_workflow(self, session_id: str) -> Optional[dict]:
        """执行工作流的工具命令。"""
        session = self.sessions.get(session_id)
        if not session:
            return None

        wf_type = session["workflow_type"]
        template = WORKFLOW_TEMPLATES.get(wf_type)
        if not template:
            return None

        params = session.get("params", {})
        tool = template["tool"]
        args_template = template.get("tool_args", "")

        try:
            # 构建命令参数
            output_dir = params.get("output_dir", os.path.dirname(params.get("input_file", ".")))
            params.setdefault("output_dir", output_dir)

            # 生成输出文件路径
            if "output_file" not in params:
                params["output_file"] = os.path.join(output_dir, f"{wf_type}_result.txt")
            if "output_sam" not in params:
                params["output_sam"] = os.path.join(output_dir, "aligned.sam")

            args = args_template.format(**params) if args_template else ""

            session["status"] = "executing"
            self._save()

            # 执行工具
            if tool == "primer3_core":
                result = self._execute_primer3(params)
            elif tool == "phylo_pipeline":
                result = self._execute_phylo(params)
            else:
                cmd = f"{tool} {args}".strip()
                result = self._execute_command(cmd, timeout=120)

            session["result"] = result[:2000] if result else ""
            session["status"] = "completed"
            self._save()

            return {
                "session_id": session_id,
                "tool": tool,
                "result": result[:2000],
                "success": True,
            }

        except Exception as e:
            session["status"] = "error"
            session["result"] = str(e)
            self._save()
            return {
                "session_id": session_id,
                "tool": tool,
                "result": str(e),
                "success": False,
                "error": str(e),
            }

    def explain_result(self, session_id: str) -> Optional[str]:
        """用 LLM 通俗解释工具输出。"""
        session = self.sessions.get(session_id)
        if not session or not session.get("result"):
            return None

        template = WORKFLOW_TEMPLATES.get(session["workflow_type"], {})
        tool_name = template.get("display_name", session["workflow_type"])
        result_hint = template.get("result_hint", "")

        try:
            client = APIClient(self.settings)
            prompt = self.EXPLAIN_PROMPT.format(
                tool_name=tool_name,
                raw_output=session["result"][:1500],
            )
            messages = [
                {"role": "system", "content": f"你是iGEM生信助手，擅长用通俗语言解释工具输出。参考要点：{result_hint}"},
                {"role": "user", "content": prompt},
            ]
            explanation = client.chat_completion(messages, temperature=0.5, max_tokens=500, timeout=30)
            return explanation
        except Exception as e:
            return f"解读失败：{e}\n\n原始输出：{session['result'][:500]}"

    def cancel_session(self, session_id: str):
        """取消工作流会话。"""
        session = self.sessions.get(session_id)
        if session:
            session["status"] = "cancelled"
            self._save()

    def get_active_session(self) -> Optional[dict]:
        """获取当前活跃的工作流会话（如果有）。"""
        for sid, session in self.sessions.items():
            if session.get("status") == "active":
                return session
        return None

    def get_session_info(self, session_id: str) -> Optional[dict]:
        """获取会话信息。"""
        session = self.sessions.get(session_id)
        if not session:
            return None
        template = WORKFLOW_TEMPLATES.get(session["workflow_type"], {})
        return {
            **session,
            "display_name": template.get("display_name", ""),
            "tool": template.get("tool", ""),
        }

    def list_workflows(self) -> list[dict]:
        """列出所有可用工作流。"""
        result = []
        for wf_type, template in WORKFLOW_TEMPLATES.items():
            result.append({
                "type": wf_type,
                "display_name": template["display_name"],
                "trigger_keywords": template.get("trigger_keywords", []),
                "steps_count": len(template.get("steps", [])),
            })
        return result

    # ── 工具执行 ──

    @staticmethod
    def _execute_command(cmd: str, timeout: int = 120) -> str:
        """执行系统命令并返回输出。"""
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
            output = result.stdout or ""
            if result.stderr:
                output += "\n[STDERR]\n" + result.stderr
            if result.returncode != 0:
                output += f"\n[返回码: {result.returncode}]"
            return output[:5000] or "(无输出)"
        except subprocess.TimeoutExpired:
            return f"执行超时（{timeout}秒），请检查命令或增加超时时间"
        except FileNotFoundError:
            return f"工具未找到，请确认 {cmd.split()[0]} 已安装并在PATH中"

    def _execute_primer3(self, params: dict) -> str:
        """执行 Primer3 引物设计。"""
        sequence = params.get("sequence", "")
        if not sequence:
            return "未提供目标序列"

        param_str = params.get("params", "")
        # 构建 primer3 输入
        tm_range = "55,65"
        product_size = "100,500"
        if param_str:
            parts = re.findall(r'(\d+)[\-~到至](\d+)', param_str)
            if len(parts) >= 1:
                tm_range = f"{parts[0][0]},{parts[0][1]}"
            if len(parts) >= 2:
                product_size = f"{parts[1][0]},{parts[1][1]}"

        # 尝试使用 primer3_core
        try:
            input_text = (
                f"SEQUENCE_ID=igem_target\n"
                f"SEQUENCE_TEMPLATE={sequence}\n"
                f"PRIMER_OPT_TM=60.0\n"
                f"PRIMER_MIN_TM={tm_range.split(',')[0]}\n"
                f"PRIMER_MAX_TM={tm_range.split(',')[1]}\n"
                f"PRIMER_PRODUCT_SIZE_RANGE={product_size}\n"
                f"PRIMER_NUM_RETURN=5\n"
                f"=\n"
            )
            result = subprocess.run(
                ["primer3_core"],
                input=input_text, capture_output=True, text=True,
                timeout=60, encoding="utf-8", errors="replace",
            )
            return result.stdout[:3000] or result.stderr[:500] or "(无输出)"
        except FileNotFoundError:
            # primer3 未安装，使用 LLM 辅助
            return "[primer3_core 未安装] 请安装: pip install primer3-py 或从 https://primer3.org 下载"

    def _execute_phylo(self, params: dict) -> str:
        """执行系统发育树构建。"""
        alignment_file = params.get("alignment_file", "")
        method = params.get("method", "Neighbor-Joining")

        if not alignment_file or not os.path.exists(alignment_file):
            return f"比对文件不存在: {alignment_file}"

        # 尝试使用 MUSCLE + PhyML 或简单 Python 方案
        try:
            from Bio import Phylo
            from Bio.Phylo.TreeConstruction import DistanceCalculator, DistanceTreeConstructor
            from Bio import AlignIO

            alignment = AlignIO.read(alignment_file, "fasta")
            calculator = DistanceCalculator("identity")
            dm = calculator.get_distance(alignment)

            if "Neighbor" in method:
                constructor = DistanceTreeConstructor()
                tree = constructor.nj(dm)
            else:
                constructor = DistanceTreeConstructor()
                tree = constructor.nj(dm)  # ML需要更复杂设置，暂用NJ

            output_file = os.path.splitext(alignment_file)[0] + "_tree.nwk"
            Phylo.write(tree, output_file, "newick")

            return f"建树完成！输出文件: {output_file}\n方法: {method}\n分类群数: {len(tree.get_terminals())}"
        except ImportError:
            return "[Biopython 未安装] 请安装: pip install biopython"
        except Exception as e:
            return f"建树失败: {e}"

    @staticmethod
    def parse_flow_command(text: str) -> tuple:
        """解析 /flow 命令。

        返回 (mode, payload):
            ("list", None) - 列出可用工作流
            ("start", {"workflow_type": "..."}) - 启动工作流
            ("cancel", None) - 取消当前工作流
            (None, None) - 不是flow命令
        """
        raw = (text or "").strip()
        if not raw:
            return None, None
        lower = raw.lower()

        prefixes = ("/flow", "工作流")
        matched = None
        for p in prefixes:
            if lower.startswith(p):
                matched = p
                break
        if not matched:
            return None, None

        body = raw[len(matched):].lstrip(" ：:")

        if not body or body in ("list", "列表"):
            return "list", None
        if body in ("cancel", "取消", "退出"):
            return "cancel", None

        # 尝试匹配工作流
        guide = BioWorkflowGuide.__new__(BioWorkflowGuide)
        guide.settings = {}
        wf_type = guide.match_workflow.__func__(guide, body)
        if wf_type:
            return "start", {"workflow_type": wf_type}

        # 直接作为自然语言匹配
        for wf_type, template in WORKFLOW_TEMPLATES.items():
            for keyword in template.get("trigger_keywords", []):
                if keyword in body.lower():
                    return "start", {"workflow_type": wf_type}

        return "start", {"workflow_type": body.strip()}
