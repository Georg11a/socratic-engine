import json
import asyncio
from engine.socratic_engine import SocraticEngine
from engine.feature_extractor import FeatureExtractor
from engine.rule_evaluator import RuleEvaluator

async def main():
    print("=== Rule Diagnosis ===\n")

    # Load configuration
    with open('config/question_triggers_config.json', 'r') as f:
        config = json.load(f)

    # Create components
    extractor = FeatureExtractor(config)
    evaluator = RuleEvaluator(config)
    engine = SocraticEngine(config)

    # Build history with 12 chart changes
    history = []
    for i in range(12):
        history.append({
            'interactionType': 'chart_type_changed',
            'x_attribute': 'party',
            'y_attribute': 'income',
            'chart_changed': ['scatterplot', 'barchart', 'linechart'][i % 3],
            'participantId': 'test_user',
        })

    # Extract features
    context = {
        'x_attribute': 'party',
        'y_attribute': 'income',
        'chart_type': 'scatterplot'
    }
    
    print("Building interaction history...\n")
    features = extractor.extract(history, context)
    
    print("=== Extracted Features ===")
    print(f"  interaction_count: {features['interaction_count']}")
    print(f"  insight_count: {features['insight_count']}")
    print(f"  chart_changes_last_20: {features['chart_changes_last_20']}")
    print(f"  chart_changes_last_30: {features['chart_changes_last_30']}")
    print(f"  axis_changes_last_20: {features['axis_changes_last_20']}")
    print(f"  rapid_chart_changes: {features['rapid_chart_changes']}")
    print(f"  no_sustained_focus: {features['no_sustained_focus']}")
    
    print("\n=== Checking Rules ===")
    
    # Check the clarity rule specifically
    clarity_config = config['triggers']['clarity']
    print(f"\nClarity Category (Priority: {clarity_config['priority']})")
    print(f"  Total conditions: {len(clarity_config['conditions'])}\n")
    
    for condition in clarity_config['conditions']:
        print(f"  Rule: {condition['name']}")
        print(f"    Detection rule: {condition['detection_rule']}")
        
        # Check if this rule would trigger
        is_match = True
        for key, constraint in condition['detection_rule'].items():
            feature_val = features.get(key)
            print(f"      {key}: {feature_val} vs {constraint}", end='')
            
            # Simple check
            if isinstance(constraint, dict):
                min_val = constraint.get('min', float('-inf'))
                max_val = constraint.get('max', float('inf'))
                if feature_val is None or not (min_val <= feature_val <= max_val):
                    is_match = False
                    print(" ❌")
                else:
                    print(" ✓")
            elif isinstance(constraint, bool):
                if feature_val != constraint:
                    is_match = False
                    print(" ❌")
                else:
                    print(" ✓")
            else:
                print()
        
        print(f"    Would trigger: {'YES ✓' if is_match else 'NO ❌'}\n")
    
    # Now test with actual engine
    print("\n=== Testing with Engine ===")
    for i in range(25):
        result = await engine.process_interaction(
            'test_user_2',
            {
                'interactionType': 'chart_type_changed',
                'x_attribute': 'party',
                'y_attribute': 'income',
                'chart_changed': ['scatterplot', 'barchart'][i % 2],
                'participantId': 'test_user_2',
            },
            context
        )
        
        if result.get('should_ask'):
            print(f"\n✓ TRIGGERED at step {i+1}!")
            print(f"  Category: {result['category']}")
            print(f"  Question: {result['question']}")
            break
        else:
            print(f"Step {i+1}: {result.get('reason')}")

if __name__ == "__main__":
    asyncio.run(main())
