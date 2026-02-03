"""
FeatureExtractor - 从用户交互历史中提取所有检测规则需要的特征

使用方式：
    from engine.feature_extractor import FeatureExtractor
    extractor = FeatureExtractor(config)
    features = extractor.extract(history, current_context)
"""

from typing import List, Dict, Any, Optional
import re


class FeatureExtractor:
    def __init__(self, config: Dict[str, Any]):
        self.windows = config['feature_extraction_specs']['windows']

    def extract(self, history: List[Dict], current_context: Dict) -> Dict[str, Any]:
        """
        主方法：提取所有特征
        
        Args:
            history: 用户的交互历史列表
            current_context: 当前状态上下文
            
        Returns:
            包含所有特征的字典
        """
        recent_20 = history[-20:] if len(history) >= 20 else history
        recent_10 = history[-10:] if len(history) >= 10 else history
        recent_5 = history[-5:] if len(history) >= 5 else history

        return {
            # ========== 基础计数特征 ==========
            'interaction_count': len(history),
            'insight_count': self._count_by_type(history, 'save_user_insight'),
            'filter_count_total': self._count_by_type(history, 'filter'),
            'aggregation_count_total': self._count_by_type(history, 'aggregation'),
            'chart_type_changed_count': self._count_by_type(history, 'chart_type_changed'),

            # ========== 时间窗口特征 ==========
            'mouseover_count_last_20': self._count_by_type(recent_20, 'mouseover_item'),
            'axis_changes_last_20': self._count_by_type(recent_20, 'axis_attribute_changed'),
            'axis_changes_last_10': self._count_by_type(recent_10, 'axis_attribute_changed'),
            'chart_changes_last_20': self._count_by_type(recent_20, 'chart_type_changed'),
            'chart_changes_last_30': self._count_by_type(history[-30:], 'chart_type_changed'),

            # ========== 持续性特征 ==========
            'same_x_attribute_last_n_steps': self._count_consecutive_same(history, 'x_attribute'),
            'same_y_attribute_last_n_steps': self._count_consecutive_same(history, 'y_attribute'),
            'same_chart_type_last_n_steps': self._count_consecutive_same(history, 'chart_type'),
            'consecutive_same_axis': self._count_consecutive_same(history, 'x_attribute'),
            'same_axis_last_25_steps': self._check_same_attribute_in_window(history, 'x_attribute', 25),

            # ========== 洞察相关特征 ==========
            'steps_since_last_insight': self._steps_since(history, 'save_user_insight'),
            'last_insight': self._get_last_insight(history),
            'last_insight_length': len(self._get_last_insight(history) or ''),
            'last_insight_no_numbers': not self._has_numbers(self._get_last_insight(history)),
            'save_insight_last_20': self._count_by_type(recent_20, 'save_user_insight') > 0,

            # ========== 当前状态 ==========
            'current_x_attribute': current_context.get('x_attribute', ''),
            'current_y_attribute': current_context.get('y_attribute', ''),
            'current_chart_type': current_context.get('chart_type', 'unknown'),
            'filters_active': current_context.get('filters_active', []),

            # ========== Mouseover相关 ==========
            'recent_mouseover_points': self._get_recent_mouseover_points(recent_20),
            'mouseover_after_insight': self._mouseover_after_insight(history),
            'no_mouseover_after_insight': self._steps_since_insight(history, 'mouseover_item') > 10,

            # ========== 探索多样性 ==========
            'unique_attributes_examined': self._get_unique_attributes(history),
            'attribute_pairs_tried': self._count_attribute_pairs(history),
            'unique_attributes_last_10': self._get_unique_attributes(recent_10),

            # ========== 行为模式识别 ==========
            'rapid_chart_changes': self._detect_rapid_changes(recent_10, 'chart_type_changed', 5),
            'no_sustained_focus': self._detect_no_sustained_focus(history),
            'diverse_interactions': self._check_diverse_interactions(history),
            'no_axis_change_after': self._steps_without_type(history, 'axis_attribute_changed') > 15,
            'no_filter_usage_after': self._steps_without_type(history, 'filter') > 10,

            # ========== 最近行为 ==========
            'recent_actions': [h.get('interaction_type', h.get('type', '')) for h in recent_5]
        }

    # ========== 辅助方法 ==========

    def _count_by_type(self, history: List[Dict], type_str: str) -> int:
        """按类型计数交互"""
        count = 0
        for h in history:
            # 支持两种命名方式：snake_case 和 camelCase
            interaction_type = h.get('interaction_type') or h.get('interactionType') or h.get('type') or ''
            if type_str in str(interaction_type):
                count += 1
        return count

    def _count_consecutive_same(self, history: List[Dict], field: str) -> int:
        """计算连续相同的次数（从最后往前）"""
        if not history:
            return 0

        count = 0
        current_value = None

        for h in reversed(history):
            value = h.get(field)

            if value is None:
                continue

            if current_value is None:
                current_value = value
                count = 1
            elif value == current_value:
                count += 1
            else:
                break

        return count

    def _check_same_attribute_in_window(self, history: List[Dict], field: str, window_size: int) -> bool:
        """检查在指定窗口内是否都是相同的属性"""
        window = history[-window_size:] if len(history) >= window_size else history
        if not window:
            return False

        first_value = None
        for h in window:
            if h.get(field):
                first_value = h[field]
                break

        if not first_value:
            return False

        return all(not h.get(field) or h[field] == first_value for h in window)

    def _steps_since(self, history: List[Dict], type_str: str) -> Optional[int]:
        """计算距离上次某类型交互的步数"""
        for i in range(len(history) - 1, -1, -1):
            interaction_type = history[i].get('interaction_type') or history[i].get('interactionType') or history[i].get('type') or ''
            if type_str in str(interaction_type):
                return len(history) - 1 - i
        return None

    def _steps_since_insight(self, history: List[Dict], type_str: str) -> int:
        """计算自上次insight后某类型交互的数量"""
        last_insight_index = self._find_last_index(history, 'save_user_insight')
        if last_insight_index == -1:
            return 0

        after_insight = history[last_insight_index + 1:]
        return self._count_by_type(after_insight, type_str)

    def _get_last_insight(self, history: List[Dict]) -> Optional[str]:
        """获取最后一条insight文本"""
        for h in reversed(history):
            if h.get('save_user_insight'):
                return h['save_user_insight']
        return None

    def _has_numbers(self, text: Optional[str]) -> bool:
        """检查文本中是否包含数字"""
        if not text:
            return False
        return bool(re.search(r'\d', text))

    def _get_recent_mouseover_points(self, recent: List[Dict]) -> List[Dict]:
        """获取最近的mouseover点"""
        points = []
        for h in recent:
            interaction_type = h.get('interaction_type') or h.get('interactionType') or h.get('type')
            if interaction_type == 'mouseover_item':
                point = {
                    'x': h.get('point_x_value'),
                    'y': h.get('point_y_value'),
                    'x_attr': h.get('point_x_attribute') or h.get('x_attribute'),
                    'y_attr': h.get('point_y_attribute') or h.get('y_attribute')
                }
                if point['x'] is not None and point['y'] is not None:
                    points.append(point)
        return points

    def _mouseover_after_insight(self, history: List[Dict]) -> int:
        """检查insight后是否有mouseover"""
        last_insight_index = self._find_last_index(history, 'save_user_insight')
        if last_insight_index == -1:
            return 0

        after_insight = history[last_insight_index + 1:]
        return self._count_by_type(after_insight, 'mouseover_item')

    def _get_unique_attributes(self, history: List[Dict]) -> List[str]:
        """获取所有探索过的唯一属性"""
        attrs = set()
        for h in history:
            if h.get('x_attribute'):
                attrs.add(h['x_attribute'])
            if h.get('y_attribute'):
                attrs.add(h['y_attribute'])
        return list(attrs)

    def _count_attribute_pairs(self, history: List[Dict]) -> int:
        """计算尝试过的属性对数量"""
        pairs = set()
        for h in history:
            x_attr = h.get('x_attribute')
            y_attr = h.get('y_attribute')
            if x_attr and y_attr:
                pair = tuple(sorted([x_attr, y_attr]))
                pairs.add(pair)
        return len(pairs)

    def _detect_rapid_changes(self, window: List[Dict], type_str: str, threshold: int) -> bool:
        """检测快速变化（窗口内变化次数超过阈值）"""
        return self._count_by_type(window, type_str) >= threshold

    def _detect_no_sustained_focus(self, history: List[Dict]) -> bool:
        """检测是否没有持续关注（频繁切换）"""
        if len(history) < 20:
            return False

        recent_20 = history[-20:]
        chart_changes = self._count_by_type(recent_20, 'chart_type_changed')
        axis_changes = self._count_by_type(recent_20, 'axis_attribute_changed')

        return (chart_changes + axis_changes) > 10

    def _check_diverse_interactions(self, history: List[Dict]) -> bool:
        """检查是否有多样化的交互类型"""
        types = set()
        for h in history:
            interaction_type = h.get('interaction_type') or h.get('interactionType') or h.get('type')
            if interaction_type:
                types.add(interaction_type)
        return len(types) >= 5

    def _steps_without_type(self, history: List[Dict], type_str: str) -> int:
        """计算最近没有某类型交互的步数"""
        for i in range(len(history) - 1, -1, -1):
            interaction_type = history[i].get('interaction_type') or history[i].get('interactionType') or history[i].get('type') or ''
            if type_str in str(interaction_type):
                return len(history) - 1 - i
        return len(history)

    def _find_last_index(self, history: List[Dict], type_str: str) -> int:
        """查找最后一个匹配类型的索引"""
        for i in range(len(history) - 1, -1, -1):
            interaction_type = history[i].get('interaction_type') or history[i].get('interactionType') or history[i].get('type') or ''
            if type_str in str(interaction_type):
                return i
        return -1
