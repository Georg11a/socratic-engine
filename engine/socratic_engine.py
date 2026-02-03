"""
SocraticEngine - 主控制器

整合所有组件，管理问题触发流程

使用方式:
    from engine.socratic_engine import SocraticEngine
    engine = SocraticEngine(config, groq_api_key)
    result = await engine.process_interaction(user_id, interaction, current_context)
"""

from typing import Dict, List, Any, Optional
from datetime import datetime
from engine.feature_extractor import FeatureExtractor
from engine.rule_evaluator import RuleEvaluator
from engine.question_generator import QuestionGenerator


class SocraticEngine:
    def __init__(self, config: Dict[str, Any], groq_api_key: Optional[str] = None):
        # 验证配置
        if not config or 'triggers' not in config:
            raise ValueError("Invalid configuration: missing 'triggers' key")
        
        self.config = config
        print('✓ Configuration loaded successfully')
        print(f"  - {len(config['triggers'])} question categories")
        print(f"  - {self._count_total_rules()} total rules")

        # 初始化核心组件
        try:
            self.feature_extractor = FeatureExtractor(config)
            self.rule_evaluator = RuleEvaluator(config)
            self.question_generator = QuestionGenerator(config, groq_api_key)
            print('✓ Engine components initialized')
        except Exception as e:
            print(f'✗ Failed to initialize components: {e}')
            raise

        # 配置参数
        self.cooldown_period = config.get('metadata', {}).get('cooldown_period', 20)
        self.priority_order = config.get('priority_rules', {}).get('rules', [{}])[0].get(
            'priority_order',
            ['clarity', 'relevance', 'accuracy', 'precision', 'depth', 'breadth']
        )

        # 用户会话存储
        self.user_sessions: Dict[str, Dict] = {}

        # 统计信息
        self.stats = {
            'total_interactions_processed': 0,
            'questions_triggered': 0,
            'questions_by_category': {},
            'cooldown_blocks': 0,
            'no_trigger_events': 0
        }

        print('✓ SocraticEngine ready')
        print(f"  - Cooldown period: {self.cooldown_period} steps")
        print(f"  - Priority order: {' > '.join(self.priority_order)}")

    async def process_interaction(
        self, 
        user_id: str, 
        interaction: Dict, 
        current_context: Dict
    ) -> Dict[str, Any]:
        """
        主方法：处理单个用户交互
        
        Args:
            user_id: 用户ID
            interaction: 交互事件对象
            current_context: 当前状态上下文
            
        Returns:
            处理结果字典
        """
        self.stats['total_interactions_processed'] += 1

        try:
            # 1. 获取或创建用户session
            session = self._get_or_create_session(user_id)

            # 2. 添加交互到历史
            session['history'].append({
                **interaction,
                'processed_at': datetime.now().isoformat()
            })

            # 3. 检查冷却期
            current_step = len(session['history'])
            if current_step - session['lastQuestionStep'] < self.cooldown_period:
                self.stats['cooldown_blocks'] += 1
                return {
                    'should_ask': False,
                    'reason': 'cooldown',
                    'steps_until_next': self.cooldown_period - (current_step - session['lastQuestionStep'])
                }

            # 4. 提取特征
            features = self.feature_extractor.extract(session['history'], current_context)

            # 5. 评估规则
            triggered_rules = self.rule_evaluator.evaluate(features)

            if not triggered_rules:
                self.stats['no_trigger_events'] += 1
                return {
                    'should_ask': False,
                    'reason': 'no_trigger',
                    'debug': {
                        'interaction_count': features['interaction_count'],
                        'insight_count': features['insight_count']
                    }
                }

            # 6. 选择最优规则
            best_rule = self._select_best_rule(triggered_rules, features)

            # 7. 生成问题
            question_result = await self.question_generator.generate(
                best_rule,
                features,
                session['history']
            )

            # 8. 更新session状态
            session['lastQuestionStep'] = current_step
            session['questionsAsked'].append({
                'step': current_step,
                'category': best_rule['category'],
                'condition': best_rule['condition_name'],
                'question': question_result['question'],
                'timestamp': datetime.now().isoformat()
            })

            # 9. 更新统计
            self.stats['questions_triggered'] += 1
            category = best_rule['category']
            self.stats['questions_by_category'][category] = \
                self.stats['questions_by_category'].get(category, 0) + 1

            return {
                'should_ask': True,
                **question_result,
                'trigger_details': {
                    'condition_name': best_rule['condition_name'],
                    'confidence': best_rule['confidence'],
                    'all_triggered': [r['condition_name'] for r in triggered_rules],
                    'total_triggered': len(triggered_rules)
                },
                'session_info': {
                    'total_interactions': len(session['history']),
                    'questions_asked': len(session['questionsAsked']),
                    'last_question_step': session['lastQuestionStep']
                }
            }

        except Exception as e:
            print(f"Error processing interaction for user {user_id}: {e}")
            return {
                'should_ask': False,
                'reason': 'error',
                'error': str(e)
            }

    def save_user_response(self, user_id: str, question_id: str, response_text: str) -> Dict:
        """保存用户对问题的回答"""
        session = self.user_sessions.get(user_id)
        
        if session:
            response_entry = {
                'interaction_type': 'question_response',
                'question_id': question_id,
                'response': response_text,
                'timestamp': datetime.now().isoformat(),
                'interaction_sequence': len(session['history']) + 1
            }

            session['history'].append(response_entry)
            
            # 找到对应的问题并标记已回答
            if session['questionsAsked']:
                last_question = session['questionsAsked'][-1]
                last_question['response'] = response_text
                last_question['responded_at'] = datetime.now().isoformat()

            return {'success': True, 'session_length': len(session['history'])}

        return {'success': False, 'error': 'Session not found'}

    def get_user_session(self, user_id: str) -> Optional[Dict]:
        """获取用户session（用于查询）"""
        return self.user_sessions.get(user_id)

    def get_active_sessions(self) -> List[Dict]:
        """获取所有活跃session的概览"""
        sessions = []
        
        for user_id, session in self.user_sessions.items():
            sessions.append({
                'userId': user_id,
                'interactionCount': len(session['history']),
                'questionsAsked': len(session['questionsAsked']),
                'lastActivity': session['history'][-1].get('timestamp') if session['history'] else None
            })

        return sessions

    def get_stats(self) -> Dict[str, Any]:
        """获取引擎统计信息"""
        active_sessions = len(self.user_sessions)
        return {
            **self.stats,
            'active_sessions': active_sessions,
            'avg_questions_per_session': self.stats['questions_triggered'] / max(1, active_sessions),
            'trigger_rate': self.stats['questions_triggered'] / max(1, self.stats['total_interactions_processed']),
            'cooldown_block_rate': self.stats['cooldown_blocks'] / max(1, self.stats['total_interactions_processed'])
        }

    def reset_user_session(self, user_id: str) -> Dict:
        """重置用户session（用于测试或新会话）"""
        if user_id in self.user_sessions:
            del self.user_sessions[user_id]
        return {'success': True, 'message': f'Session reset for user {user_id}'}

    def clear_all_sessions(self) -> Dict:
        """清除所有session（谨慎使用）"""
        count = len(self.user_sessions)
        self.user_sessions.clear()
        return {'success': True, 'message': f'Cleared {count} sessions'}

    # ========== 私有方法 ==========

    def _get_or_create_session(self, user_id: str) -> Dict:
        """获取或创建用户session"""
        if user_id not in self.user_sessions:
            self.user_sessions[user_id] = {
                'userId': user_id,
                'history': [],
                'lastQuestionStep': -999,
                'questionsAsked': [],
                'createdAt': datetime.now().isoformat()
            }
        return self.user_sessions[user_id]

    def _select_best_rule(self, triggered_rules: List[Dict], features: Dict) -> Dict:
        """从多个触发规则中选择最优的"""
        # 1. 检查特殊boost情况
        boosted_rule = self._check_boost_conditions(triggered_rules, features)
        if boosted_rule:
            return boosted_rule

        # 2. 按priority_order和confidence排序
        def get_priority(rule):
            try:
                return self.priority_order.index(rule['category'])
            except ValueError:
                return 999

        return min(triggered_rules, key=lambda r: (get_priority(r), -r['confidence']))

    def _check_boost_conditions(self, triggered_rules: List[Dict], features: Dict) -> Optional[Dict]:
        """检查特殊boost条件"""
        # Boost 1: 刚保存insight，优先depth/breadth
        if features.get('insight_count', 0) > 0:
            steps_since = features.get('steps_since_last_insight')
            if steps_since is not None and 1 <= steps_since <= 5:
                depth_breadth = [r for r in triggered_rules if r['category'] in ['depth', 'breadth']]
                if depth_breadth:
                    return max(depth_breadth, key=lambda r: r['confidence'])

        # Boost 2: 用户卡住很久，优先breadth
        if features.get('same_x_attribute_last_n_steps', 0) > 40:
            breadth = next((r for r in triggered_rules if r['category'] == 'breadth'), None)
            if breadth:
                breadth['confidence'] *= 1.2
                return breadth

        # Boost 3: 早期探索，优先clarity
        if features.get('interaction_count', 0) < 30 and features.get('insight_count', 0) == 0:
            clarity = next((r for r in triggered_rules if r['category'] == 'clarity'), None)
            if clarity:
                clarity['confidence'] *= 1.2
                return clarity

        return None

    def _count_total_rules(self) -> int:
        """计算配置文件中的总规则数"""
        count = 0
        for category_config in self.config['triggers'].values():
            count += len(category_config['conditions'])
        return count

    def export_session_data(self, user_id: str) -> Optional[Dict]:
        """导出session数据（用于分析）"""
        session = self.user_sessions.get(user_id)
        if not session:
            return None

        return {
            'userId': session['userId'],
            'totalInteractions': len(session['history']),
            'questionsAsked': len(session['questionsAsked']),
            'history': session['history'],
            'questions': session['questionsAsked'],
            'createdAt': session['createdAt'],
            'exportedAt': datetime.now().isoformat()
        }

    def export_all_sessions(self) -> Dict:
        """批量导出所有session（用于研究）"""
        all_sessions = []
        
        for user_id in self.user_sessions:
            session_data = self.export_session_data(user_id)
            if session_data:
                all_sessions.append(session_data)

        return {
            'totalSessions': len(all_sessions),
            'exportedAt': datetime.now().isoformat(),
            'stats': self.get_stats(),
            'sessions': all_sessions
        }
