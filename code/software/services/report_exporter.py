from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib import font_manager
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import ndcg_score

from air_governance.common.config import resolve_project_root
from air_governance.models.risk_model import RiskModel, trend_labels
from air_governance.models.ranker_model import RankerModel


def _configure_cjk() -> None:
    preferred = ['Noto Sans CJK SC', 'AR PL UMing CN', 'DejaVu Sans']
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in preferred:
        if name in available:
            plt.rcParams['font.sans-serif'] = [name]
            break
    plt.rcParams['axes.unicode_minus'] = False


_configure_cjk()


class ReportExporter:
    def __init__(self, root: str | Path, backend):
        self.root = resolve_project_root(root)
        self.backend = backend
        self.fig_dir = self.root / 'outputs' / 'report_figures'
        self.tab_dir = self.root / 'outputs' / 'report_tables'
        self.fig_dir.mkdir(parents=True, exist_ok=True)
        self.tab_dir.mkdir(parents=True, exist_ok=True)
        self.risk_metrics = json.loads((self.root/'outputs/metrics/risk_metrics.json').read_text(encoding='utf-8'))
        self.rank_metrics = json.loads((self.root/'outputs/metrics/rank_metrics.json').read_text(encoding='utf-8'))
        self.risk_model = RiskModel.load(self.root/'models/risk')
        self.rank_model = RankerModel.load(self.root/'models/ranker')
        self.risk_test = pd.read_csv(self.root/'data/processed/risk_dataset.csv').query("split == 'test'").copy()
        self.rank_test = pd.read_csv(self.root/'data/processed/rank_dataset.csv').query("split == 'test'").copy()
        self.manifest_rows: list[dict[str, Any]] = []

    def _fig(self):
        fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')
        return fig, ax

    def _save(self, fig, filename: str, data_source: str, source_type: str, model_version: str = '') -> Path:
        path = self.fig_dir / filename
        fig.tight_layout()
        fig.savefig(path, dpi=100, facecolor='white')
        plt.close(fig)
        self.manifest_rows.append({
            'figure_id': filename.split('_', 1)[0],
            'filename': filename,
            'generator_script': 'scripts/export_report_evidence.py',
            'data_source': data_source,
            'model_version': model_version,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'source_type': source_type,
        })
        return path

    @staticmethod
    def _trend_text(v) -> str:
        return v.value if hasattr(v, 'value') else str(v)

    def export_tables(self) -> list[Path]:
        paths = []
        # 1 risk metrics
        p = self.tab_dir/'risk_metrics.csv'
        pd.DataFrame([self.risk_metrics]).to_csv(p,index=False); paths.append(p)
        # 2 rank metrics + programmatic relative improvement
        r = dict(self.rank_metrics)
        rule = float(r['mean_selected_utility_rule']); ranker = float(r['mean_selected_utility_ranker'])
        r['relative_improvement_percent'] = (ranker-rule)/rule*100.0
        p = self.tab_dir/'rank_metrics.csv'; pd.DataFrame([r]).to_csv(p,index=False); paths.append(p)
        # 3 decision trace from unified DB
        rows = self.backend.db.query(
            "SELECT decision_id,task_id,region_id,current_risk,predicted_risk,trend,task_score,rank_position,selected,policy,safety_result "
            "FROM candidate_tasks ORDER BY id"
        )
        p = self.tab_dir/'decision_trace.csv'; pd.DataFrame(rows).to_csv(p,index=False); paths.append(p)
        # 4 safety trace
        rows = self.backend.db.query(
            "SELECT timestamp,decision_id,task_id,region_id,event_type,input_task,result,reason,fallback FROM safety_events ORDER BY id"
        )
        p = self.tab_dir/'safety_trace.csv'; pd.DataFrame(rows).to_csv(p,index=False); paths.append(p)
        # 5 closed loop cycles
        rows = self.backend.db.query(
            "SELECT timestamp,decision_id,task_id,region_id,event,fan_level,pre_risk,post_risk,source_type FROM purification_events ORDER BY id"
        )
        p = self.tab_dir/'closed_loop_cycles.csv'; pd.DataFrame(rows).to_csv(p,index=False); paths.append(p)
        return paths

    def export_figures(self) -> list[Path]:
        files: list[Path] = []
        self.manifest_rows = []
        risk_meta = json.loads((self.root/'models/risk/model_metadata.json').read_text(encoding='utf-8'))
        rank_meta = json.loads((self.root/'models/ranker/model_metadata.json').read_text(encoding='utf-8'))
        risk_ver = risk_meta.get('model_version','risk_xgb_v1')
        rank_ver = rank_meta.get('model_version','ranker_xgb_v1')

        # 01 System overview
        fig, ax = self._fig(); ax.axis('off')
        ax.set_title('智驭新风统一软件架构与闭环', fontsize=26, loc='left', pad=20)
        labels = [
            ('多源环境采样',.18,.73),('区域时序状态',.50,.73),('XGBoost 风险预测',.82,.73),
            ('SafetySupervisor',.18,.48),('Ranker / RuleBasedPolicy',.50,.48),('候选任务生成',.82,.48),
            ('TaskManager',.18,.23),('Mock Navigation / Purification',.50,.23),('治理后复测',.82,.23),
        ]
        for label,x,y in labels:
            ax.add_patch(FancyBboxPatch((x-.12,y-.065),.24,.13,boxstyle='round,pad=0.01',facecolor='#F4F6F8',edgecolor='#67727E',linewidth=1.2,transform=ax.transAxes))
            ax.text(x,y,label,ha='center',va='center',fontsize=12,transform=ax.transAxes,wrap=True)
        def arrow(a,b):
            ax.annotate('',xy=b,xytext=a,xycoords=ax.transAxes,textcoords=ax.transAxes,arrowprops=dict(arrowstyle='->',lw=1.4))
        arrow((.30,.73),(.38,.73)); arrow((.62,.73),(.70,.73)); arrow((.82,.665),(.82,.545))
        arrow((.70,.48),(.62,.48)); arrow((.38,.48),(.30,.48)); arrow((.18,.415),(.18,.295))
        arrow((.30,.23),(.38,.23)); arrow((.62,.23),(.70,.23))
        ax.annotate('',xy=(.08,.73),xytext=(.94,.23),xycoords=ax.transAxes,textcoords=ax.transAxes,arrowprops=dict(arrowstyle='->',lw=1.2,connectionstyle='arc3,rad=.35'))
        ax.text(.05,.08,'统一核心：Phase-1 数据模型、Risk/Ranker、规则策略、安全监督、状态机、适配器与 BrainLogStore；GUI/回放/报告导出仅作为服务层包装。',transform=ax.transAxes,fontsize=13)
        files.append(self._save(fig,'01_system_overview.png','src/air_governance + services/gui_backend.py','SOFTWARE_IN_THE_LOOP',f'{risk_ver};{rank_ver}'))

        # 02 Spatial risk map from final backend
        snap=self.backend.snapshot(); states=snap.regions
        fig,ax=self._fig(); ax.set_title('空间污染状态：当前风险 / 预测风险',fontsize=24,loc='left')
        vals=[float(s.predicted_risk) for s in states.values()]; lo,hi=min(vals),max(vals); hi=hi if hi>lo else lo+1
        norm=plt.Normalize(lo,hi); cmap=plt.get_cmap('YlOrRd')
        for d in self.backend.scene['regions']:
            s=states[d['region_id']]; v=float(s.predicted_risk)
            ax.add_patch(Rectangle((d['x'],d['y']),d['w'],d['h'],facecolor=cmap(norm(v)),edgecolor='#444',linewidth=1.2))
            ax.text(d['cx'],d['cy'],f"{d['name']} ({d['region_id']})\n当前 {s.current_risk:.1f}\n预测 {s.predicted_risk:.1f}\n{self._trend_text(s.trend)}",ha='center',va='center',fontsize=13)
        ax.set_xlim(-1.5,5.5); ax.set_ylim(-1.5,5.5); ax.set_aspect('equal'); ax.set_xlabel('x / m（模拟）'); ax.set_ylabel('y / m（模拟）')
        fig.colorbar(plt.cm.ScalarMappable(norm=norm,cmap=cmap),ax=ax,label='预测风险')
        files.append(self._save(fig,'02_spatial_risk_map.png','outputs/logs/report_evidence.db + Phase-1 RegionState','SOFTWARE_IN_THE_LOOP',risk_ver))

        # 03 Risk prediction on final test split
        pred=self.risk_model.predict(self.risk_test); n=min(360,len(self.risk_test))
        fig,ax=self._fig(); ax.plot(np.arange(n),self.risk_test.future_risk.to_numpy()[:n],label='真实标签（模拟教师）'); ax.plot(np.arange(n),pred[:n],label='模型预测'); ax.legend(); ax.set_xlabel('测试样本'); ax.set_ylabel('风险'); ax.set_title('短时风险预测：最终测试集',fontsize=24,loc='left')
        ax.text(.01,.02,f"MAE={self.risk_metrics['mae']:.6f}  RMSE={self.risk_metrics['rmse']:.6f}  R²={self.risk_metrics['r2']:.6f}  Trend Accuracy={self.risk_metrics['trend_accuracy']:.6f}",transform=ax.transAxes,fontsize=12)
        files.append(self._save(fig,'03_risk_prediction.png','data/processed/risk_dataset.csv (split=test)','SIMULATION',risk_ver))

        # 04 Ranker decision example
        did=self.rank_test.decision_id.iloc[0]; g=self.rank_test[self.rank_test.decision_id==did].copy(); g['task_score']=self.rank_model.predict(g); g=g.sort_values('task_score',ascending=False)
        fig,ax=self._fig(); x=np.arange(len(g)); w=.36; ax.bar(x-w/2,g.ground_truth_utility,w,label='ground-truth utility'); ax.bar(x+w/2,g.task_score,w,label='Ranker score'); ax.set_xticks(x,g.region_id); ax.legend(); ax.set_title(f'任务排序示例：{did}',fontsize=24,loc='left'); ax.set_xlabel('区域'); ax.set_ylabel('得分')
        ax.text(.01,.02,f"NDCG@3={self.rank_metrics['ndcg@3']:.6f}  Precision@3={self.rank_metrics['precision@3']:.6f}  Top-1={self.rank_metrics['top1_accuracy']:.6f}",transform=ax.transAxes,fontsize=12)
        files.append(self._save(fig,'04_ranker_decision.png','data/processed/rank_dataset.csv (split=test)','SIMULATION',rank_ver))

        # 05 feature importance - risk model
        imp=self.risk_model.model.feature_importances_; order=np.argsort(imp)[-15:]
        fig,ax=self._fig(); ax.barh([self.risk_model.feature_columns[i] for i in order],imp[order]); ax.set_title('风险模型特征重要度（Top 15）',fontsize=24,loc='left'); ax.set_xlabel('feature importance')
        files.append(self._save(fig,'05_feature_importance.png','models/risk/model.json + feature_columns.json','SIMULATION',risk_ver))

        # 06 SHAP-style exact XGBoost contributions from risk model
        sample=self.risk_test[self.risk_model.feature_columns].head(300)
        booster=self.risk_model.model.get_booster(); dm=xgb.DMatrix(sample,feature_names=self.risk_model.feature_columns); contrib=booster.predict(dm,pred_contribs=True)[:,:-1]
        top=np.argsort(np.mean(np.abs(contrib),axis=0))[-12:]
        rng=np.random.default_rng(20260909)
        fig,ax=self._fig()
        for yi,j in enumerate(top):
            ax.scatter(contrib[:,j],np.full(len(contrib),yi)+rng.normal(0,.07,len(contrib)),s=13,alpha=.35)
        ax.axvline(0,linewidth=1); ax.set_yticks(range(len(top)),[self.risk_model.feature_columns[j] for j in top]); ax.set_xlabel('XGBoost exact contribution'); ax.set_title('SHAP/Tree Contribution 汇总（风险模型）',fontsize=24,loc='left')
        files.append(self._save(fig,'06_shap_summary.png','risk_dataset test sample + XGBoost pred_contribs','SIMULATION',risk_ver))

        # 07 Rule vs Ranker
        rule=float(self.rank_metrics['mean_selected_utility_rule']); ranker=float(self.rank_metrics['mean_selected_utility_ranker']); rel=(ranker-rule)/rule*100.0
        fig,ax=self._fig(); bars=ax.bar(['RuleBasedPolicy','XGBoost Ranker'],[rule,ranker]); ax.set_ylabel('mean simulated utility'); ax.set_title('规则策略与学习排序策略对照',fontsize=24,loc='left')
        for b,v in zip(bars,[rule,ranker]): ax.text(b.get_x()+b.get_width()/2,v,f'{v:.6f}',ha='center',va='bottom',fontsize=14)
        ax.text(.03,.90,f'Relative improvement = {rel:.6f}%',transform=ax.transAxes,fontsize=15)
        files.append(self._save(fig,'07_rule_vs_ranker.png','data/processed/rank_dataset.csv + scripts/train_ranker.py','SIMULATION',rank_ver))

        # 08 Closed loop timeline
        events=pd.DataFrame(self.backend.db.query("SELECT id,timestamp,task_id,decision_id,COALESCE(region,region_id,'') AS region,COALESCE(state,new_state,'') AS state,COALESCE(event,reason,'') AS event,cycle FROM task_state_events ORDER BY id"))
        fig,ax=self._fig(); ax.axis('off'); ax.set_title('闭环任务状态机时间线',fontsize=24,loc='left'); y=.90
        for _,row in events.tail(24).iterrows():
            ax.text(.03,y,f"{row['task_id'] or '-':<12}  C{row['cycle'] if pd.notna(row['cycle']) else '-'}  {row['state']:<12}  {row['region'] or '-':<4}  {row['event']}",transform=ax.transAxes,fontsize=11); y-=.035
        ax.text(.03,.03,'状态来自 Phase-1 TaskStateMachine / TaskManager；导航与治理为 Mock 软件在环执行。',transform=ax.transAxes,fontsize=12)
        files.append(self._save(fig,'08_closed_loop_timeline.png','outputs/logs/report_evidence.db: task_state_events','SOFTWARE_IN_THE_LOOP','task_state_machine_v1'))

        # 09 Safety fallback. Prefer the actual MODEL_ERROR trace created by the unified evidence run.
        safety=pd.DataFrame(self.backend.db.query("SELECT * FROM safety_events ORDER BY id"))
        model_evt=safety[safety.event_type=='MODEL_ERROR'].tail(1) if (not safety.empty and 'event_type' in safety) else pd.DataFrame()
        fig,ax=self._fig(); ax.axis('off'); ax.set_title('安全监督与模型故障降级',fontsize=24,loc='left')
        if not model_evt.empty:
            row=model_evt.iloc[0]
            items=[('事件','MODEL_ERROR'),('输入任务',str(row.input_task)),('安全结果',str(row.result)),('降级动作',str(row.fallback))]
            reason=str(row.reason)
        else:
            items=[('事件','NORMAL'),('检查入口','TaskManager'),('安全结果','Phase-1 Supervisor'),('降级动作','RuleBasedPolicy')]; reason='无 MODEL_ERROR 日志；图仅展示代码路径。'
        xs=[.07,.29,.51,.73]
        for x,(k,v) in zip(xs,items):
            ax.add_patch(Rectangle((x,.52),.18,.20,transform=ax.transAxes,facecolor='#F4F6F8',edgecolor='#777'))
            ax.text(x+.012,.665,k,transform=ax.transAxes,fontsize=12,fontweight='bold')
            ax.text(x+.012,.59,str(v),transform=ax.transAxes,fontsize=11,wrap=True)
        ax.add_patch(Rectangle((.07,.32),.84,.12,transform=ax.transAxes,facecolor='#FAFAFA',edgecolor='#999'))
        ax.text(.085,.395,'原因 / 证据链',transform=ax.transAxes,fontsize=11,fontweight='bold')
        ax.text(.085,.35,reason,transform=ax.transAxes,fontsize=10.5,wrap=True)
        ax.text(.07,.19,'MODEL_ERROR：Ranker 不参与选择，统一 runtime 切换 Phase-1 RuleBasedPolicy；候选任务仍由 Phase-1 SafetySupervisor 进行确定性安全检查。',transform=ax.transAxes,fontsize=12)
        ax.text(.07,.11,'数据来源：统一软件在环运行日志；Mock Navigation / Mock Purification 不等同于实机导航或实机净化。',transform=ax.transAxes,fontsize=11)
        files.append(self._save(fig,'09_safety_fallback.png','outputs/logs/report_evidence.db: safety_events + unified runtime','SOFTWARE_IN_THE_LOOP','safety_supervisor_v1'))

        # 10 residuals
        resid=pred-self.risk_test.future_risk.to_numpy(float)
        fig,ax=self._fig(); ax.hist(resid,bins=34,edgecolor='white'); ax.axvline(0,linewidth=1); ax.set_xlabel('Prediction - Ground truth'); ax.set_ylabel('count'); ax.set_title('风险预测残差分布',fontsize=24,loc='left')
        files.append(self._save(fig,'10_prediction_residuals.png','data/processed/risk_dataset.csv (split=test)','SIMULATION',risk_ver))

        # 11 trend accuracy by class
        current=self.risk_test.current_risk.to_numpy(float); true=trend_labels(current,self.risk_test.future_risk.to_numpy(float)); pclass=trend_labels(current,pred)
        labels=[(-1,'FALLING'),(0,'STABLE'),(1,'RISING')]; acc=[]; counts=[]
        for code,name in labels:
            mask=true==code; counts.append(int(mask.sum())); acc.append(float(np.mean(pclass[mask]==true[mask])) if mask.any() else 0.0)
        fig,ax=self._fig(); bars=ax.bar([x[1] for x in labels],np.array(acc)*100); ax.set_ylim(0,100); ax.set_ylabel('accuracy (%)'); ax.set_title('趋势分类准确率（按类别）',fontsize=24,loc='left')
        for b,a,nc in zip(bars,acc,counts): ax.text(b.get_x()+b.get_width()/2,a*100,f'{a*100:.1f}%\nn={nc}',ha='center',va='bottom')
        files.append(self._save(fig,'11_trend_accuracy.png','data/processed/risk_dataset.csv (split=test)','SIMULATION',risk_ver))

        # 12 NDCG distribution: exact groups on test split
        scores=self.rank_model.predict(self.rank_test); tmp=self.rank_test.copy(); tmp['score']=scores; ndcgs=[]
        for _,gg in tmp.groupby('decision_id',sort=False): ndcgs.append(float(ndcg_score([gg.relevance_grade.to_numpy(float)],[gg.score.to_numpy(float)],k=min(3,len(gg)))))
        fig,ax=self._fig(); ax.hist(ndcgs,bins=np.linspace(0.5,1.0,21),edgecolor='white'); ax.axvline(float(np.mean(ndcgs)),linestyle='--',linewidth=2,label=f"mean={np.mean(ndcgs):.6f}"); ax.legend(); ax.set_xlabel('NDCG@3'); ax.set_ylabel('query groups'); ax.set_title('NDCG@3 测试查询组分布',fontsize=24,loc='left')
        files.append(self._save(fig,'12_ndcg_distribution.png','data/processed/rank_dataset.csv (split=test)','SIMULATION',rank_ver))

        # 13 standalone utility
        fig,ax=self._fig(); bars=ax.bar(['Rule baseline','XGBoost Ranker'],[rule,ranker]); ax.set_ylabel('mean simulated utility'); ax.set_title('Ranker vs Rule Utility',fontsize=24,loc='left')
        for b,v in zip(bars,[rule,ranker]): ax.text(b.get_x()+b.get_width()/2,v,f'{v:.6f}',ha='center',va='bottom',fontsize=14)
        ax.text(.03,.90,f'程序计算相对提升：{rel:.6f}%',transform=ax.transAxes,fontsize=15)
        files.append(self._save(fig,'13_ranker_vs_rule_utility.png','outputs/metrics/rank_metrics.json','SIMULATION',rank_ver))

        # 14 cycle risk change
        cyc=pd.DataFrame(self.backend.db.query("SELECT region_id,pre_risk,post_risk FROM purification_events ORDER BY id LIMIT 3"))
        fig,ax=self._fig()
        if not cyc.empty:
            x=np.arange(len(cyc)); w=.34; ax.bar(x-w/2,cyc.pre_risk,w,label='治理前预测风险'); ax.bar(x+w/2,cyc.post_risk,w,label='治理后复测预测风险'); ax.set_xticks(x,[f"Cycle {i+1}\n{r}" for i,r in enumerate(cyc.region_id)]); ax.legend()
        ax.set_ylabel('risk'); ax.set_title('3 个闭环治理周期风险变化',fontsize=24,loc='left'); ax.text(.01,.02,'Mock Purification / Simulation，不代表真实净化率。',transform=ax.transAxes,fontsize=11)
        files.append(self._save(fig,'14_cycle_risk_change.png','outputs/logs/report_evidence.db: purification_events','SOFTWARE_IN_THE_LOOP',risk_ver))

        manifest=self.fig_dir/'manifest.csv'
        with manifest.open('w',newline='',encoding='utf-8-sig') as f:
            w=csv.DictWriter(f,fieldnames=['figure_id','filename','generator_script','data_source','model_version','generated_at','source_type']); w.writeheader(); w.writerows(self.manifest_rows)
        return files

    def export_all(self) -> dict[str,list[str]]:
        tables=self.export_tables(); figs=self.export_figures()
        return {'figures':[str(p) for p in figs],'tables':[str(p) for p in tables]}
