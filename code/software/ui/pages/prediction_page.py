from __future__ import annotations
from tkinter import ttk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


class PredictionPage(ttk.Frame):
    def __init__(self, master, metrics):
        super().__init__(master,padding=10); self.metrics=metrics
        ttk.Label(self,text="风险预测",font=("Arial",18,"bold")).pack(anchor="w")
        ttk.Label(self,text="数据来源：SIMULATION；固定指标来自 Phase-1 SOFTWARE-IN-THE-LOOP 测试证据",foreground="#666").pack(anchor="w")
        self.fig,self.ax=plt.subplots(figsize=(8,5)); self.canvas=FigureCanvasTkAgg(self.fig,master=self); self.canvas.get_tk_widget().pack(fill="both",expand=True)
        m=metrics; ttk.Label(self,text=f"MAE {m['risk_mae']:.6f}   RMSE {m['risk_rmse']:.6f}   R² {m['risk_r2']:.6f}   Trend Accuracy {m['trend_accuracy']*100:.4f}%",font=("Arial",12,"bold")).pack(anchor="w",pady=6)
        self.history=[]
    def refresh(self,snapshot,backend):
        if snapshot.regions:
            s=max(snapshot.regions.values(),key=lambda x:x.current_risk); self.history.append((s.current_risk,s.predicted_risk)); self.history=self.history[-100:]
        self.ax.clear();
        if self.history:
            t=list(range(len(self.history))); self.ax.plot(t,[x[0] for x in self.history],label="True/Current Risk"); self.ax.plot(t,[x[1] for x in self.history],label="Predicted Risk"); self.ax.legend(); self.ax.set_xlabel("refresh step"); self.ax.set_ylabel("risk")
        self.canvas.draw_idle()
