# BidBox

## AI-assisted public procurement investigation

BidBox is an AI procurement agent designed to help government procurement committees analyze public tenders with greater speed, consistency, and access to evidence.

The agent investigates tender and award data, supplier history, and relevant price patterns. It then produces a clear, sourced report that highlights potential issues, unusual patterns, and questions that deserve human attention.

BidBox is **decision support, not decision replacement**. It does not award tenders, select suppliers, or make final procurement decisions. Its role is to help a committee understand the available evidence before the committee applies its own professional judgment, procurement rules, and due process.

## The problem

Public procurement committees often have to review large volumes of information under significant time pressure. Important signals can be difficult to identify when they are spread across:

- Tender notices and requirements
- Bid submissions and award records
- Supplier ownership, participation, and performance history
- Historical contract prices and comparable procurements
- Public procurement and regulatory data

Manual review can make it difficult to consistently compare suppliers, detect outliers, trace evidence, and document why a concern was raised. A missed pattern does not necessarily indicate wrongdoing, but it can mean that a committee does not have the right question or evidence at the right time.

## The idea

BidBox acts as an investigative layer between public procurement data and the human committee reviewing a tender.

For a selected tender, the agent can:

1. **Understand the procurement**  
   Read the tender context, requirements, bidders, evaluation information, and award details.

2. **Gather relevant evidence**  
   Use connected tools and data sources to inspect tender and award records, supplier history, and comparable pricing information.

3. **Look for potential signals**  
   Identify unusual prices, repeated supplier relationships, inconsistent participation patterns, conflicts that may require review, or other anomalies supported by the available data.

4. **Explain what it found**  
   Generate a report that links each observation to its source, explains why it may matter, and distinguishes evidence from interpretation.

5. **Support human review**  
   Give the committee practical follow-up questions and evidence to verify. The committee remains responsible for interpreting the findings and making the final decision.

## What a report should provide

A BidBox report is intended to be useful in a real committee workflow. It should make the following clear:

- **Finding:** What was observed?
- **Evidence:** Which record, source, or comparison supports the observation?
- **Context:** How does the observation relate to this tender?
- **Potential significance:** Why might the committee want to investigate it?
- **Uncertainty:** What cannot be concluded from the available data?
- **Recommended follow-up:** What should a human reviewer verify next?

The goal is not to label a supplier as fraudulent or automatically reject a bid. An anomaly is a reason to ask a better question, not proof of misconduct.

## Core principles

### Human accountability

BidBox does not make the final award decision. Procurement committees retain authority and responsibility for decisions, explanations, and compliance with applicable laws and procedures.

### Evidence before conclusions

Every material observation should be traceable to a source or an explicit comparison. The agent should avoid unsupported claims, hidden reasoning, and conclusions that cannot be independently checked.

### Investigative, not punitive

The system highlights potential risks and anomalies for review. It must not present an unverified signal as proof of corruption, collusion, fraud, or supplier wrongdoing.

### Explainable uncertainty

Public data may be incomplete, outdated, inconsistent, or difficult to compare. Reports should communicate data limitations, confidence, and alternative explanations instead of presenting false certainty.

### Consistent scrutiny

The same types of checks should be available across tenders and suppliers. The system should support fair, consistent review rather than selectively targeting particular suppliers or organizations.

### Auditable use

Investigations should preserve the tender being reviewed, the sources consulted, the checks performed, the report generated, and any human follow-up. This creates a defensible record of how evidence was considered.

## What BidBox is not

BidBox is not:

- An autonomous tender evaluation or award system
- A replacement for procurement officers, auditors, or legal review
- A definitive fraud or corruption detector
- A private blacklist or supplier scoring system without due process
- A system that should act on an unsupported allegation

## Intended users

BidBox is intended for people and institutions involved in public procurement oversight, including:

- Government procurement committees
- Procurement and contract management officers
- Internal audit and compliance teams
- Anti-corruption and public accountability bodies
- Civil society and investigative organizations working with public procurement data

## Early product vision

The initial focus is a trustworthy investigation workflow:

1. Select or import a public tender.
2. Review the tender and participating suppliers.
3. Ask BidBox to investigate relevant records and patterns.
4. Inspect cited evidence and data limitations.
5. Export a structured report for committee review.
6. Record human conclusions and follow-up actions separately from the AI's findings.

Future capabilities may include broader procurement data coverage, stronger supplier relationship analysis, configurable investigation checklists, historical price benchmarking, and integrations with existing government procurement systems.

## Success looks like

BidBox should help a procurement committee:

- Spend less time searching across disconnected records
- Notice potentially important patterns earlier
- Ask more precise follow-up questions
- Compare evidence more consistently
- Produce clearer, better-sourced review records
- Keep final authority and accountability with humans

The measure of success is not how many alerts the agent produces. It is whether the agent helps people make better-informed, fairer, and more accountable procurement decisions.

## Status

BidBox is currently an early-stage concept and prototype. This README describes the intended product direction; implementation details, supported data sources, and evaluation criteria will evolve as the project develops.
