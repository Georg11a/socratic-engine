"""
RuleEvaluator - 评估哪些触发规则满足条件

使用方式：
    from engine.rule_evaluator import RuleEvaluator
    evaluator = RuleEvaluator(config)
    triggered_rules = evaluator.evaluate(features)
"""

from typing import List, Dict, Any


class RuleEvaluator:
    def __init__(self, config: Dict[str, Any]):
        self.triggers = config['triggers']

    def evaluate(self, features: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        主方法：评估所有规则
        
        Args:
            features: 从FeatureExtractor提取的特征字典
            
        Returns:
            触发的规则列表
        """
        triggered_rules = []

        # 遍历6大类别
        for category, category_config in self.triggers.items():
            # 遍历每个类别的所有条件
            for condition in category_config['conditions']:
                if self._check_condition(condition['detection_rule'], features):
                    triggered_rules.append({
                        'category': category,
                        'condition_name': condition['name'],
                        'description': condition.get('description', ''),
                        'priority': category_config['priority'],
                        'question_template': condition['question_template'],
                        'confidence': self._calculate_confidence(condition, features)
                    })

        return triggered_rules

    def _check_condition(self, rule: Dict, features: Dict) -> bool:
        """检查单个条件是否满足"""
        try:
            for key, constraint in rule.items():
                if not self._check_single_constraint(key, constraint, features):
                    return False
            return True
        except Exception as e:
            print(f"Error checking condition: {e}")
            return False

    def _check_single_constraint(self, key: str, constraint: Any, features: Dict) -> bool:
        """检查单个约束条件"""
        feature_value = features.get(key)

        # 处理特殊的复合约束
        if key == 'rapid_chart_changes':
            return self._check_rapid_changes(constraint, features)

        if key == 'last_insight_contains':
            return self._check_text_contains(features.get('last_insight'), constraint)

        if key == 'no_specific_numbers':
            return constraint == features.get('last_insight_no_numbers')

        if key == 'no_sustained_focus':
            return features.get('no_sustained_focus') == constraint

        if key == 'diverse_interactions':
            return features.get('diverse_interactions') == constraint

        # 处理标准约束类型
        if isinstance(constraint, dict) and not isinstance(constraint, list):
            return self._check_range_constraint(feature_value, constraint)

        if isinstance(constraint, bool):
            return feature_value == constraint

        if isinstance(constraint, (int, float)):
            return feature_value == constraint

        if isinstance(constraint, list):
            return self._check_array_constraint(key, constraint, features)

        return True

    def _check_range_constraint(self, value: Any, constraint: Dict) -> bool:
        """检查范围约束 {min: 10, max: 30}"""
        if value is None:
            return False

        if 'min' in constraint and value < constraint['min']:
            return False

        if 'max' in constraint and value > constraint['max']:
            return False

        return True

    def _check_text_contains(self, text: str, words: List[str]) -> bool:
        """检查文本包含约束"""
        if not text:
            return False
        lower_text = text.lower()
        return any(word.lower() in lower_text for word in words)

    def _check_array_constraint(self, key: str, constraint: List, features: Dict) -> bool:
        """检查数组约束（列表包含）"""
        # 处理 last_insight_contains 类型
        if 'contains' in key:
            text_key = key.replace('_contains', '')
            text = features.get(text_key, '')
            return self._check_text_contains(text, constraint)

        # 处理 contains_general_terms
        if key == 'contains_general_terms':
            return self._check_text_contains(features.get('last_insight'), constraint)

        return False

    def _check_rapid_changes(self, constraint: Any, features: Dict) -> bool:
        """检查快速变化约束"""
        if isinstance(constraint, dict):
            # 这个信息在features中应该已经计算好了
            return features.get('rapid_chart_changes') == True
        return features.get('rapid_chart_changes') == constraint

    def _calculate_confidence(self, condition: Dict, features: Dict) -> float:
        """计算触发置信度"""
        rule = condition['detection_rule']
        total_constraints = 0
        strong_matches = 0

        for key, constraint in rule.items():
            total_constraints += 1
            feature_value = features.get(key)

            # 检查是否是强匹配（远离边界）
            if isinstance(constraint, dict) and not isinstance(constraint, list):
                if 'min' in constraint and feature_value and feature_value > constraint['min'] * 1.5:
                    strong_matches += 1
                elif 'max' in constraint and feature_value and feature_value < constraint['max'] * 0.75:
                    strong_matches += 1
            else:
                strong_matches += 1  # 其他类型的约束都算强匹配

        # 基础置信度 + 强匹配加成
        base_confidence = 0.75
        match_bonus = (strong_matches / total_constraints) * 0.2 if total_constraints > 0 else 0

        return min(base_confidence + match_bonus, 0.99)

    def get_all_rules(self) -> List[Dict]:
        """获取所有可能触发的规则（用于调试）"""
        all_rules = []

        for category, category_config in self.triggers.items():
            for condition in category_config['conditions']:
                all_rules.append({
                    'category': category,
                    'name': condition['name'],
                    'description': condition.get('description', '')
                })

        return all_rules

    def check_specific_rule(self, category: str, condition_name: str, features: Dict) -> Dict:
        """检查特定规则是否会触发（用于测试）"""
        category_config = self.triggers.get(category)
        if not category_config:
            return None

        condition = None
        for cond in category_config['conditions']:
            if cond['name'] == condition_name:
                condition = cond
                break

        if not condition:
            return None

        triggered = self._check_condition(condition['detection_rule'], features)

        return {
            'triggered': triggered,
            'confidence': self._calculate_confidence(condition, features) if triggered else 0,
            'condition': condition
        }
