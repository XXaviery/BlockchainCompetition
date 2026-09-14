from __future__ import annotations
import tkinter as tk
from tkinter import ttk

COLS=["region_id","current_risk","predicted_risk","trend","waiting_time","distance","estimated_move_time","estimated_energy","battery_soc","switch_cost","task_score","valid_flag","safety_result"]

class DecisionPage(ttk.Frame):
    def __init__(self, master, metrics, policy_callback):
        super().__init__(master,padding=10); self.metrics=metrics
        top=ttk.Frame(self); top.pack(fill="x"); ttk.Label(top,text="任务排序与决策解释",font=("Arial",18,"bold")).pack(side="left")
        self.mode=tk.StringVar(value="ranker"); ttk.Radiobutton(top,text="学习排序策略",variable=self.mode,value="ranker",command=lambda:policy_callback("ranker")).pack(side="right"); ttk.Radiobutton(top,text="规则策略",variable=self.mode,value="rule",command=lambda:policy_callback("rule")).pack(side="right")
        self.tree=ttk.Treeview(self,columns=COLS,show="headings",height=12)
        for c in COLS: self.tree.heading(c,text=c); self.tree.column(c,width=105,anchor="center")
        self.tree.tag_configure("selected", background="#E8EEF5")
        self.tree.pack(fill="both",expand=True,pady=8)
        self.selected_label=tk.StringVar(value="当前选择任务：—"); ttk.Label(self,textvariable=self.selected_label,font=("Arial",12,"bold")).pack(anchor="w")
        self.compare=tk.StringVar(value="尚未产生 decision_id"); ttk.Label(self,textvariable=self.compare,font=("Arial",11,"bold")).pack(anchor="w")
        m=metrics; ttk.Label(self,text=f"NDCG@3={m['ranker_ndcg_at_3']:.6f}   Precision@3={m['ranker_precision_at_3']:.6f}   Top-1 Accuracy={m['ranker_top1_accuracy']:.6f}   source=SIMULATION",foreground="#555").pack(anchor="w",pady=4)
    def refresh(self,snapshot,backend):
        for x in self.tree.get_children(): self.tree.delete(x)
        for i,row in enumerate(backend.candidate_rows()):
            vals=[f"{row[c]:.3f}" if isinstance(row[c],float) else row[c] for c in COLS]; iid=self.tree.insert("","end",values=vals,tags=("selected",) if row.get("selected") else ())
            if row.get("selected"): self.tree.selection_set(iid); self.tree.focus(iid)
        selected = snapshot.selected_task.region_id if snapshot.selected_task else "—"
        self.selected_label.set(f"当前选择任务：{selected}")
        c=backend.policy_comparison(); self.compare.set(f"decision_id={c['decision_id']} | RuleBasedPolicy → {c['rule_selection']} | Ranker → {c['ranker_selection']} | 目标差异={'是' if c['different'] else '否'} | active={c['active_policy']}")
