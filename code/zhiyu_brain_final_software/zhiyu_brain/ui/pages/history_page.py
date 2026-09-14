from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from services.replay_service import ReplayService

class HistoryPage(ttk.Frame):
    def __init__(self,master,db_path):
        super().__init__(master,padding=10); self.replay=ReplayService(db_path)
        bar=ttk.Frame(self); bar.pack(fill="x"); ttk.Label(bar,text="闭环任务与日志",font=("Arial",18,"bold")).pack(side="left")
        self.task=tk.StringVar(); self.combo=ttk.Combobox(bar,textvariable=self.task,state="readonly",width=16); self.combo.pack(side="right"); self.combo.bind("<<ComboboxSelected>>",lambda e:self.refresh_task())
        self.tree=ttk.Treeview(self,columns=["timestamp","task_id","decision_id","region","state","event"],show="headings")
        for c in ["timestamp","task_id","decision_id","region","state","event"]: self.tree.heading(c,text=c); self.tree.column(c,width=150 if c!="event" else 340)
        self.tree.pack(fill="both",expand=True,pady=8)
    def refresh_task(self):
        tid=self.task.get(); rows=self.replay.task_timeline(tid) if tid else self.replay.full_timeline();
        for x in self.tree.get_children(): self.tree.delete(x)
        for r in rows: self.tree.insert("","end",values=[r.get(c,"") for c in ["timestamp","task_id","decision_id","region","state","event"]])
    def refresh(self,snapshot,backend):
        ids=self.replay.task_ids(); self.combo["values"]=ids
        if ids and not self.task.get(): self.task.set(ids[-1])
        self.refresh_task()
