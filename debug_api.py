from hob_junter.core.linkedin import fetch_linkedin_jobs

def test_full_cycle():
    # Това ще зареди токена от inputs.json автоматично
    jobs = fetch_linkedin_jobs(
        query="Project Manager",
        locations=["Sofia, Bulgaria"],
        limit=3 # Малък лимит за тест
    )
    
    print("\nFINAL RESULTS:")
    for j in jobs:
        print(f"Job: {j.title} @ {j.company}")
        print(f"Desc Preview: {j.description[:100]}...")
        print("-" * 20)

if __name__ == "__main__":
    test_full_cycle()