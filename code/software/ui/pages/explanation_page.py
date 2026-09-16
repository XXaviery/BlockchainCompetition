from __future__ import annotations
from tkinter import ttk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

class ExplanationPage(ttk.Frame):
    def __init__(self,master):
        super().__init__(master,padding=10); ttk.Label(self,text="模型解释",font=("Arial",18,"bold")).pack(anchor="w"); ttk.Label(self,text="任务优先级主要影响因素",foreground="#555").pack(anchor="w")
        self.fig,self.ax=plt.subplots(figsize=(8,4)); self.canvas=FigureCanvasTkAgg(self.fig,master=self); self.canvas.get_tk_widget().pack(fill="both",expand=True)
        self.local=ttk.Label(self,text="尚未选择任务",justify="left"); self.local.pack(anchor="w",pady=8)
    def refresh(self,snapshot,backend):
        imp=backend.feature_importance(); items=sorted(imp.items(),key=lambda kv:kv[1]); self.ax.clear(); self.ax.barh([k for k,_ in items],[v for _,v in items]); self.ax.set_title("全局特征重要度"); self.canvas.draw_idle()
        ex=backend.local_explanation(); pos="\n".join(f"+ {k}: {v:+.4f}" for k,v in ex['positive']); neg="\n".join(f"- {k}: {v:+.4f}" for k,v in ex['negative']); self.local.configure(text=f"当前被选中任务的局部解释\n提高 task_score 的因素：\n{pos or '—'}\n降低 task_score 的因素：\n{neg or '—'}")
