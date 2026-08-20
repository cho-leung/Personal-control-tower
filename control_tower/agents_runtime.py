"""Optional API-side agent twins. The bus remains the authority."""
def build_agents():
    try:
        from agents import Agent
    except ImportError as e:
        raise RuntimeError("Run: pip install -r requirements-agents.txt") from e
    producer=Agent(
        name="Research Producer",
        instructions="Act only as PRODUCER inside explicit scope. Do not audit or authorize."
    )
    auditor=Agent(
        name="Independent Research Auditor",
        instructions="Audit only the frozen artifact. Do not repair or authorize."
    )
    controller=Agent(
        name="Research Controller",
        instructions="Track state, lineage, handoffs and gates. Do not produce mathematics."
    )
    return {"producer":producer,"auditor":auditor,"controller":controller}
