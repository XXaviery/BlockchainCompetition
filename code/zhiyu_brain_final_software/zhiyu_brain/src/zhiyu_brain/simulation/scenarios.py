from __future__ import annotations

SCENARIOS = {
    'scenario_1_kitchen_pm_spike': {
        'name': '厨房突然出现 PM2.5 高峰',
        'events': [
            {'region': 'C', 'pollutant': 'pm25', 'start': 18, 'end': 32, 'rate': 9.0},
            {'region': 'C', 'pollutant': 'voc', 'start': 20, 'end': 30, 'rate': 15.0},
        ],
    },
    'scenario_2_bedroom_voc_rise': {
        'name': '卧室 VOC 持续上升但厨房瞬时浓度更高',
        'events': [
            {'region': 'B', 'pollutant': 'voc', 'start': 12, 'end': 60, 'rate': 10.0},
            {'region': 'C', 'pollutant': 'pm25', 'start': 28, 'end': 36, 'rate': 14.0},
        ],
    },
    'scenario_3_multi_region': {
        'name': '三个区域同时异常，距离、电量和等待时间不同',
        'events': [
            {'region': 'A', 'pollutant': 'pm25', 'start': 16, 'end': 44, 'rate': 5.5},
            {'region': 'B', 'pollutant': 'voc', 'start': 20, 'end': 55, 'rate': 8.5},
            {'region': 'D', 'pollutant': 'co2', 'start': 15, 'end': 65, 'rate': 24.0},
            {'region': 'D', 'pollutant': 'voc', 'start': 32, 'end': 58, 'rate': 5.0},
        ],
    },
}
