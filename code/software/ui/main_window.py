from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from services.report_exporter import ReportExporter
from ui.pages.overview_page import OverviewPage
from ui.pages.spatial_page import SpatialPage
from ui.pages.prediction_page import PredictionPage
from ui.pages.decision_page import DecisionPage
from ui.pages.explanation_page import ExplanationPage
from ui.pages.history_page import HistoryPage


class MainWindow:
    def __init__(self, backend, auto_demo: bool = False):
        self.backend = backend
        self.root = tk.Tk()
        self.root.title("智驭新风 — 上位机与可视化演示系统")
        self.root.geometry("1500x900")
        self.root.minsize(1200, 760)
        self._configure_style()
        self._build()
        self.refresh()
        if auto_demo:
            self.root.after(600, self.start_demo)

    def _configure_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook.Tab", padding=(14, 8))
        style.configure("TButton", padding=(10, 5))

    def _build(self):
        header = ttk.Frame(self.root, padding=(12, 8)); header.pack(fill="x")
        ttk.Label(header, text="智驭新风", font=("Arial", 20, "bold")).pack(side="left")
        ttk.Label(header, text="环境风险预测与安全任务调度的移动空气治理机器人 | 软件在环", foreground="#555").pack(side="left", padx=16)
        self.cycle_var = tk.StringVar(value="completed_cycles=0")
        ttk.Label(header, textvariable=self.cycle_var).pack(side="right")

        controls = ttk.Frame(self.root, padding=(12, 4)); controls.pack(fill="x")
        ttk.Button(controls, text="开始演示", command=self.start_demo).pack(side="left", padx=3)
        ttk.Button(controls, text="暂停", command=self.pause).pack(side="left", padx=3)
        ttk.Button(controls, text="继续", command=self.resume).pack(side="left", padx=3)
        ttk.Button(controls, text="单步", command=self.single_step).pack(side="left", padx=3)
        ttk.Button(controls, text="重置", command=self.reset).pack(side="left", padx=3)
        ttk.Button(controls, text="导出报告图", command=self.export_figures).pack(side="left", padx=(14,3))
        ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Label(controls, text="安全事件模拟：").pack(side="left")
        self.safety_var = tk.StringVar(value="NORMAL")
        ttk.Combobox(controls, textvariable=self.safety_var, values=["NORMAL","LOW_BATTERY","LOCALIZATION_ERROR","FORBIDDEN_ZONE","MODEL_ERROR"], state="readonly", width=20).pack(side="left", padx=3)
        ttk.Button(controls, text="触发", command=self.trigger_safety).pack(side="left", padx=3)

        self.nb = ttk.Notebook(self.root); self.nb.pack(fill="both", expand=True, padx=10, pady=8)
        m = self.backend.evidence["phase1_metrics"]
        self.pages = [
            OverviewPage(self.nb), SpatialPage(self.nb), PredictionPage(self.nb, m),
            DecisionPage(self.nb, m, self.set_policy), ExplanationPage(self.nb),
            HistoryPage(self.nb, self.backend.db.db_path)
        ]
        names = ["1 系统总览","2 空间污染状态","3 风险预测","4 任务排序与决策解释","5 模型解释","6 闭环任务与日志"]
        for p,n in zip(self.pages,names): self.nb.add(p,text=n)
        self.status = tk.StringVar(value="Ready | SIMULATION / SOFTWARE-IN-THE-LOOP")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w", padding=4).pack(fill="x", side="bottom")
        self.demo_running = False

    def refresh(self):
        snap = self.backend.snapshot()
        self.cycle_var.set(f"completed_cycles={snap.completed_cycles} | final_state={snap.final_state}")
        for p in self.pages:
            try: p.refresh(snap, self.backend)
            except Exception as e: self.status.set(f"UI refresh warning: {e}")
        self.root.update_idletasks()

    def _demo_tick(self):
        if not self.demo_running: return
        if self.backend.paused:
            self.root.after(300, self._demo_tick); return
        snap = self.backend.step(force=True); self.refresh()
        if snap.final_state == "COMPLETED":
            self.demo_running = False; self.status.set("Demo completed | completed_cycles>=3 | final_state=COMPLETED")
            return
        self.root.after(650, self._demo_tick)

    def start_demo(self):
        if self.backend.robot.state == "COMPLETED": self.backend.reset(clear_db=True)
        self.backend.resume(); self.demo_running = True; self.status.set("Demo running — SIMULATION")
        self.root.after(10, self._demo_tick)

    def pause(self): self.backend.pause(); self.status.set("Paused")
    def resume(self): self.backend.resume(); self.demo_running = True; self.status.set("Running"); self.root.after(10,self._demo_tick)
    def single_step(self): self.backend.step(force=True); self.refresh(); self.status.set("Single-step advanced one key state")
    def reset(self): self.demo_running=False; self.backend.reset(clear_db=True); self.refresh(); self.status.set("Reset complete")
    def set_policy(self, mode): self.backend.set_policy(mode); self.refresh(); self.status.set(f"Policy switched to {mode}")
    def trigger_safety(self): self.backend.inject_safety_event(self.safety_var.get()); self.status.set(f"Safety event queued: {self.safety_var.get()}")
    def export_figures(self):
        try:
            out = ReportExporter(self.backend.root, self.backend).export_all()
            self.status.set(f"Export complete: {len(out['figures'])} PNG + {len(out['tables'])} CSV")
            messagebox.showinfo("导出报告图", f"已生成 {len(out['figures'])} 张 PNG 和 {len(out['tables'])} 个 CSV。\n输出目录：outputs/")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def run(self): self.root.mainloop()
