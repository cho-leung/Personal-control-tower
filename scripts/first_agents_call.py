from agents import Runner
from control_tower.agents_runtime import build_agents

def main():
    a=build_agents()
    prod=Runner.run_sync(a["producer"],"Synthetic test only. Produce a tiny non-real candidate artifact.")
    print("=== PRODUCER ===\n",prod.final_output)
    audit=Runner.run_sync(a["auditor"],"Audit this synthetic output only:\n\n"+str(prod.final_output))
    print("\n=== AUDITOR ===\n",audit.final_output)

if __name__=="__main__": main()
