"""
QuestionGenerator - 生成个性化的Socratic问题

支持两种模式:
1. 模板填充（快速、稳定）
2. LLM生成（灵活、智能）

使用方式:
    from engine.question_generator import QuestionGenerator
    generator = QuestionGenerator(config, groq_api_key)
    result = await generator.generate(rule, features, history)
"""

from typing import Dict, List, Any, Optional
import re

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("⚠ Groq not installed, will use template-only generation")


class QuestionGenerator:
    def __init__(self, config: Dict[str, Any], groq_api_key: Optional[str] = None):
        self.config = config
        self.llm_client = None
        
        # 初始化LLM客户端（如果提供了API key）
        if groq_api_key and GROQ_AVAILABLE:
            try:
                self.llm_client = Groq(api_key=groq_api_key)
                print('✓ LLM client initialized (complex scenarios will use AI generation)')
            except Exception as e:
                print(f'✗ Failed to initialize LLM client: {e}')
                print('  Will fall back to template-based generation')
        else:
            print('ℹ No Groq API key provided, using template-based generation only')
        
        self.template_vars = config.get('template_variables', [])
        self.decision_logic = config.get('decision_logic', {})

    async def generate(self, triggered_rule: Dict, features: Dict, history: Optional[List] = None) -> Dict[str, Any]:
        """
        主方法：生成问题
        
        Args:
            triggered_rule: 触发的规则对象
            features: 特征对象
            history: 交互历史（可选，复杂场景需要）
            
        Returns:
            生成的问题结果
        """
        is_simple = self._is_simple_case(triggered_rule, features)
        
        try:
            if is_simple or not self.llm_client:
                # 简单场景或没有LLM：使用模板
                question = self._generate_from_template(triggered_rule, features)
                method = 'template'
            else:
                # 复杂场景且有LLM：尝试AI生成
                question = await self._generate_from_llm(triggered_rule, features, history)
                method = 'llm'
        except Exception as e:
            print(f'Error in question generation, falling back to template: {e}')
            question = self._generate_from_template(triggered_rule, features)
            method = 'template-fallback'

        return {
            'question': self._clean_question(question),
            'category': triggered_rule['category'],
            'method': method,
            'context': self._extract_context(features),
            'template_used': triggered_rule['question_template']
        }

    def _is_simple_case(self, rule: Dict, features: Dict) -> bool:
        """判断是否为简单场景（使用模板即可）"""
        checks = {
            'interaction_count_low': features.get('interaction_count', 0) < 50,
            'insight_short': len(features.get('last_insight') or '') < 80,
            'simple_category': rule['category'] in ['clarity', 'breadth'],
        }

        # 如果满足2个以上条件，认为是简单场景
        matched_checks = sum(1 for v in checks.values() if v)
        return matched_checks >= 2

    def _generate_from_template(self, rule: Dict, features: Dict) -> str:
        """从模板生成问题"""
        template = rule['question_template']

        # 准备所有可能的替换变量
        replacements = {
            '{x_attribute}': self._format_attribute(features.get('current_x_attribute')),
            '{y_attribute}': self._format_attribute(features.get('current_y_attribute')),
            '{n_steps}': str(features.get('same_x_attribute_last_n_steps', 0)),
            '{insight_summary}': self._summarize_insight(features.get('last_insight')),
            '{insight_topic}': self._extract_insight_topic(features.get('last_insight')),
            '{attribute_list}': self._format_attribute_list(features.get('unique_attributes_examined', [])),
            '{point_x_value}': self._get_last_mouseover_x(features),
            '{point_y_value}': self._get_last_mouseover_y(features),
            '{current_chart_type}': features.get('current_chart_type', 'chart'),
            '{vague_phrase}': self._extract_vague_phrase(features.get('last_insight')),
            '{vague_word}': self._extract_vague_phrase(features.get('last_insight')),
            '{comparative_phrase}': self._extract_comparative_phrase(features.get('last_insight')),
            '{attribute}': self._format_attribute(features.get('current_x_attribute')),
            '{value}': self._get_last_mouseover_x(features),
            '{statement}': self._summarize_insight(features.get('last_insight')),
            '{unused_chart_type}': self._suggest_unused_chart_type(features),
            '{suggested_attributes}': self._suggest_attributes(features),
            '{last_insight_summary}': self._summarize_insight(features.get('last_insight')),
            '{current_attributes}': f"{self._format_attribute(features.get('current_x_attribute'))} and {self._format_attribute(features.get('current_y_attribute'))}",
            '{stated_goal}': 'your analysis goal',
            '{primary_attribute}': self._format_attribute(features.get('current_x_attribute')),
            '{primary_finding}': self._summarize_insight(features.get('last_insight')),
            '{observation}': self._summarize_insight(features.get('last_insight')),
            '{attribute_1}': self._format_attribute(features.get('current_x_attribute')),
            '{attribute_2}': self._format_attribute(features.get('current_y_attribute')),
            '{aggregate_finding}': self._summarize_insight(features.get('last_insight')),
            '{filtered_attribute}': 'the filtered attribute',
            '{unused_features}': ', '.join(self._identify_unused_features(features))
        }

        # 执行替换
        result = template
        for placeholder, value in replacements.items():
            if placeholder in result:
                result = result.replace(placeholder, str(value))

        return result

    async def _generate_from_llm(self, rule: Dict, features: Dict, history: Optional[List]) -> str:
        """使用LLM生成问题"""
        recent_actions = ', '.join((features.get('recent_actions') or [])[-5:])
        
        prompt = f"""You are helping a user analyze voter data using the Lumos visualization tool.

CURRENT SITUATION:
- Total interactions: {features.get('interaction_count', 0)}
- Insights saved: {features.get('insight_count', 0)}
- Current view: {features.get('current_x_attribute', 'unknown')} vs {features.get('current_y_attribute', 'unknown')}
- Chart type: {features.get('current_chart_type', 'unknown')}
- Last insight: "{features.get('last_insight', 'None yet')}"
- Recent actions: {recent_actions}

BEHAVIORAL PATTERN DETECTED: {rule['condition_name']}
QUESTION CATEGORY: {rule['category']}

TASK: Generate a {rule['category']} question that:
1. References their specific exploration context (attributes, values, actions)
2. Encourages deeper thinking about their analysis
3. Is conversational and natural (not robotic)
4. Is concise (under 30 words)
5. Follows this general idea: "{rule['question_template']}"

IMPORTANT: 
- Use specific values and attributes from their context
- Don't be generic - reference what they're actually doing
- Be encouraging, not judgmental

Question:"""

        try:
            response = self.llm_client.chat.completions.create(
                model='llama-3.3-70b-versatile',
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.8,
                max_tokens=100,
                top_p=0.9
            )

            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f'LLM generation failed: {e}')
            # Fallback to template
            return self._generate_from_template(rule, features)

    # ========== 辅助方法 ==========

    def _format_attribute(self, attr: Optional[str]) -> str:
        """格式化属性名为友好显示"""
        if not attr:
            return 'this attribute'
        return attr.replace('_', ' ')

    def _format_attribute_list(self, attrs: List[str]) -> str:
        """格式化属性列表"""
        if not attrs or len(attrs) == 0:
            return 'various attributes'
        return ', '.join(self._format_attribute(a) for a in attrs[:4])

    def _summarize_insight(self, insight: Optional[str]) -> str:
        """总结insight文本"""
        if not insight:
            return 'your observation'
        return insight[:80] + '...' if len(insight) > 80 else insight

    def _extract_insight_topic(self, insight: Optional[str]) -> str:
        """提取insight主题"""
        if not insight:
            return 'this'
        words = insight.split()
        return ' '.join(words[:5]) + ('...' if len(words) > 5 else '')

    def _extract_vague_phrase(self, insight: Optional[str]) -> str:
        """提取模糊措辞"""
        if not insight:
            return 'this pattern'
        
        vague_words = ['generally', 'seems', 'appears', 'roughly', 'tend to', 'kind of', 'somewhat']
        lower_insight = insight.lower()
        
        for word in vague_words:
            if word in lower_insight:
                return word
        
        return 'this pattern'

    def _extract_comparative_phrase(self, insight: Optional[str]) -> str:
        """提取比较性措辞"""
        if not insight:
            return 'the comparison'
        
        comparatives = ['more than', 'less than', 'higher', 'lower', 'greater', 'smaller']
        lower_insight = insight.lower()
        
        for phrase in comparatives:
            if phrase in lower_insight:
                start_idx = lower_insight.index(phrase)
                return insight[max(0, start_idx - 10):min(len(insight), start_idx + len(phrase) + 10)]
        
        return 'the comparison'

    def _get_last_mouseover_x(self, features: Dict) -> str:
        """获取最后一个mouseover的X值"""
        points = features.get('recent_mouseover_points', [])
        if not points:
            return ''
        return str(points[-1].get('x', ''))

    def _get_last_mouseover_y(self, features: Dict) -> str:
        """获取最后一个mouseover的Y值"""
        points = features.get('recent_mouseover_points', [])
        if not points:
            return ''
        return str(points[-1].get('y', ''))

    def _suggest_unused_chart_type(self, features: Dict) -> str:
        """建议未使用的图表类型"""
        current_chart = features.get('current_chart_type', '').lower()
        all_charts = ['bar chart', 'line chart', 'scatter plot', 'strip plot']
        
        for chart in all_charts:
            if chart.replace(' ', '') not in current_chart:
                return chart
        
        return 'different chart type'

    def _suggest_attributes(self, features: Dict) -> str:
        """建议未探索的属性"""
        used = features.get('unique_attributes_examined', [])
        all_attrs = ['age', 'income', 'gender', 'party', 'location', 'race']
        
        unused = [attr for attr in all_attrs if attr not in used]
        return ', '.join(unused[:3]) if unused else 'other attributes'

    def _identify_unused_features(self, features: Dict) -> List[str]:
        """识别未使用的功能"""
        unused = []
        
        if features.get('filter_count_total', 0) == 0:
            unused.append('filters')
        if features.get('aggregation_count_total', 0) == 0:
            unused.append('aggregations')
        if features.get('chart_type_changed_count', 0) < 2:
            unused.append('different chart types')
        
        return unused if unused else ['additional features']

    def _extract_context(self, features: Dict) -> Dict[str, Any]:
        """提取上下文信息"""
        return {
            'x_attribute': features.get('current_x_attribute'),
            'y_attribute': features.get('current_y_attribute'),
            'chart_type': features.get('current_chart_type'),
            'interaction_count': features.get('interaction_count'),
            'insight_count': features.get('insight_count')
        }

    def _clean_question(self, question: str) -> str:
        """清理问题文本"""
        # 移除Markdown格式、多余空格等
        cleaned = re.sub(r'```', '', question)
        cleaned = re.sub(r'\*\*', '', cleaned)
        cleaned = re.sub(r'\n+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    def get_status(self) -> Dict[str, Any]:
        """获取生成器状态（用于监控）"""
        return {
            'llm_available': self.llm_client is not None,
            'model': 'llama-3.3-70b-versatile' if self.llm_client else 'none',
            'mode': 'hybrid' if self.llm_client else 'template-only'
        }
