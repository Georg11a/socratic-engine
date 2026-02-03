import json
import asyncio
from engine.socratic_engine import SocraticEngine

async def main():
    print("=== Quick Trigger Test (30 steps) ===\n")

    # Load configuration
    with open('config/question_triggers_config.json', 'r') as f:
        config = json.load(f)

    # Create engine
    engine = SocraticEngine(config)

    # Simulate rapid chart changes
    print("Simulating 30 rapid interactions...\n")

    result = None
    for i in range(30):
        result = await engine.process_interaction(
            'test_user_quick',
            {
                'interactionType': 'chart_type_changed',
                'x_attribute': 'party',
                'y_attribute': 'income',
                'chart_changed': ['scatterplot', 'barchart', 'linechart'][i % 3],
                'participantId': 'test_user_quick',
                'interactionAt': f"2024-01-01T00:00:{i:02d}"
            },
            {
                'x_attribute': 'party',
                'y_attribute': 'income',
                'chart_type': ['scatterplot', 'barchart', 'linechart'][i % 3]
            }
        )
        
        status = "TRIGGERED!" if result.get('should_ask') else result.get('reason', 'processing')
        print(f"Step {i+1:2d}: {status}")
        
        if result.get('should_ask'):
            print(f"\n✓ SUCCESS - Question triggered!")
            print(f"  Category: {result['category']}")
            print(f"  Question: {result['question']}")
            print(f"  Condition: {result['trigger_details']['condition_name']}")
            print(f"  Confidence: {result['trigger_details']['confidence']:.2f}")
            print(f"  All triggered rules: {result['trigger_details']['all_triggered']}")
            break

    if not result.get('should_ask'):
        print("\n⚠ No question triggered in 30 steps")
        print("\nDebugging info:")
        session = engine.get_user_session('test_user_quick')
        if session:
            print(f"  History length: {len(session['history'])}")
            print(f"  Last question step: {session['lastQuestionStep']}")
            print(f"  Questions asked: {len(session['questionsAsked'])}")

    # Show stats
    print("\n=== Engine Stats ===")
    stats = engine.get_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")

if __name__ == "__main__":
    asyncio.run(main())
