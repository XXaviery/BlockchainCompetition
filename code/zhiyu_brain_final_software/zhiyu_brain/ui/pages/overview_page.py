from __future__ import annotations
import tkinter as tk
from tkinter import ttk


class OverviewPage(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=16)
        self.status_vars = {}
        self.metric_vars = {}
        title = ttk.Label(self, text="系统总览", font=("Arial", 18, "bold")); title.pack(anchor="w")
        ttk.Label(self, text="SIMULATION / SOFTWARE-IN-THE-LOOP", foreground="#666").pack(anchor="w", pady=(0,10))
        grid = ttk.Frame(self); grid.pack(fill="x")
        keys = ["state","timestamp","decision_id","task_id","current_region","target_region","battery_soc","fan_level","model_status","navigation_status","purification_status","safety_status","policy_mode"]
        labels = ["系统运行状态","当前时间","decision_id","task_id","当前区域","目标区域","机器人电量","风机档位","模型状态","导航状态","治理状态","安全状态","策略"]
        for i,(k,lbl) in enumerate(zip(keys,labels)):
            f=ttk.LabelFrame(grid,text=lbl,padding=8); f.grid(row=i//4,column=i%4,padx=4,pady=4,sticky="nsew")
            v=tk.StringVar(value="—"); self.status_vars[k]=v; ttk.Label(f,textvariable=v,font=("Arial",12,"bold")).pack()
        for c in range(4): grid.columnconfigure(c,weight=1)
        lower=ttk.Frame(self); lower.pack(fill="both",expand=True,pady=(10,0))
        env=ttk.LabelFrame(lower,text="当前环境指标",padding=10); env.pack(side="left",fill="both",expand=True,padx=(0,6))
        for k,lbl in [("pm25","PM2.5"),("voc","VOC"),("co2","CO₂"),("temperature","温度"),("humidity","湿度")]:
            row=ttk.Frame(env); row.pack(fill="x",pady=3); ttk.Label(row,text=lbl,width=14).pack(side="left"); v=tk.StringVar(value="—"); self.metric_vars[k]=v; ttk.Label(row,textvariable=v,font=("Arial",12,"bold")).pack(side="left")
        task=ttk.LabelFrame(lower,text="任务选择依据",padding=10); task.pack(side="left",fill="both",expand=True,padx=(6,0))
        self.task_text=tk.Text(task,height=10,width=45,wrap="word"); self.task_text.pack(fill="both",expand=True); self.task_text.configure(state="disabled")

    def refresh(self, snapshot, backend):
        r=snapshot.robot
        d=r.to_dict()
        for k,v in self.status_vars.items():
            val=d.get(k,"—")
            if k=="battery_soc": val=f"{float(val):.1f}%"
            v.set(str(val or "—"))
        st=backend.region_states.get(r.current_region)
        if st:
            vals=st.current_values
            self.metric_vars["pm25"].set(f"{vals['pm25']:.1f} μg/m³")
            self.metric_vars["voc"].set(f"{vals['voc']:.3f}")
            self.metric_vars["co2"].set(f"{vals['co2']:.0f} ppm")
            self.metric_vars["temperature"].set(f"{vals['temperature']:.1f} °C")
            self.metric_vars["humidity"].set(f"{vals['humidity']:.1f} %")
        c=snapshot.selected_task
        text="尚未产生候选任务"
        if c:
            trend = c.trend.value if hasattr(c.trend, 'value') else str(c.trend)
            text=f"目标区域：{c.region_id}\n预测风险：{c.predicted_risk:.2f}\n趋势：{trend}\ntask_score：{c.task_score:.4f}\n选择原因：当前策略={r.policy_mode}，安全检查={c.safety_result}"
        self.task_text.configure(state="normal"); self.task_text.delete("1.0","end"); self.task_text.insert("1.0",text); self.task_text.configure(state="disabled")
