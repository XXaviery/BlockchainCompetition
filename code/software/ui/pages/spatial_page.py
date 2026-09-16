from __future__ import annotations
import tkinter as tk
from tkinter import ttk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle


class SpatialPage(ttk.Frame):
    def __init__(self, master):
        super().__init__(master,padding=10)
        bar=ttk.Frame(self); bar.pack(fill="x")
        ttk.Label(bar,text="空间污染状态",font=("Arial",18,"bold")).pack(side="left")
        self.mode=tk.StringVar(value="current_risk")
        ttk.Combobox(bar,textvariable=self.mode,values=["current_risk","predicted_risk","PM2.5","VOC","CO₂"],state="readonly",width=18).pack(side="right")
        self.fig,self.ax=plt.subplots(figsize=(8,5)); self.cbar=None; self.canvas=FigureCanvasTkAgg(self.fig,master=self); self.canvas.get_tk_widget().pack(fill="both",expand=True)

    def refresh(self,snapshot,backend):
        self.ax.clear(); mode=self.mode.get(); values={}
        for s in snapshot.regions.values():
            values[s.region_id] = s.current_risk if mode=="current_risk" else s.predicted_risk if mode=="predicted_risk" else s.current_values["pm25"] if mode=="PM2.5" else s.current_values["voc"] if mode=="VOC" else s.current_values["co2"]
        lo=min(values.values()); hi=max(values.values());
        if abs(hi-lo)<1e-9: hi=lo+1
        norm=plt.Normalize(lo,hi); cmap=plt.get_cmap("YlOrRd")
        for d in backend.scene["regions"]:
            val=values[d["region_id"]]; self.ax.add_patch(Rectangle((d["x"],d["y"]),d["w"],d["h"],facecolor=cmap(norm(val)),edgecolor="#333")); self.ax.text(d["cx"],d["cy"],f"{d['name']}\n{val:.2f}",ha="center",va="center")
            if d["region_id"]==snapshot.robot.target_region: self.ax.plot(d["cx"],d["cy"],"k*",markersize=14)
        cur=next(d for d in backend.scene["regions"] if d["region_id"]==snapshot.robot.current_region); self.ax.plot(cur["cx"],cur["cy"],"ko",fillstyle="none",markersize=10)
        self.ax.set_xlim(-.2,7.2); self.ax.set_ylim(-.2,6.2); self.ax.set_aspect("equal"); self.ax.set_title(f"区域污染空间状态图 — {mode}")
        if self.cbar is not None:
            self.cbar.remove()
        sm=plt.cm.ScalarMappable(norm=norm,cmap=cmap)
        self.cbar=self.fig.colorbar(sm,ax=self.ax,fraction=.035,pad=.02)
        self.cbar.set_label(f"{mode} 数值范围")
        self.fig.tight_layout(); self.canvas.draw_idle()
