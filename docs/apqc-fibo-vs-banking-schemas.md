# APQC and FIBO vs. the `banking_*` KG extraction schemas

Why external business/financial ontologies (APQC's Banking PCF, EDM Council's FIBO) are not
suitable as a basis for knowledge-graph extraction from real bank documents, where their
vocabulary overlaps with the `domains/schemas/banking_*.yaml` schemas in ways that can cause
confusion, and how each can still be used productively — as a post-hoc completeness/reference
check rather than a schema design template.

## APQC Banking Process Classification Framework (PCF)

### Why it isn't adequate for KG extraction

APQC's PCF answers "what activities does a bank perform?" It's a taxonomy of *work* —
Category → Process Group → Process → Activity → Task — with a label at each node and,
optionally, a benchmarking metric attached. It was never designed to answer "what facts,
obligations, and relationships are stated in *this specific document*?" That's a different
job, and the mismatch shows up in a few concrete ways:

- **No entity/instance model.** APQC has a task node like *"Perform Know Your Customer (KYC)
  verification."* That's a label, not a schema. It has no notion of "extract each distinct
  KYC failure as a separate node with `refresh_frequency`, `edd_trigger`,
  `consequence_of_failure`." The `banking_sop_guides` schema does exactly that for
  `KYC_VERIFICATION`. A taxonomy node can't be populated from text — it has no properties, no
  relationship vocabulary, no extraction rules (canonical naming, aliasing, "don't
  hallucinate," context snippets). That machinery is specific to the KG-extraction problem
  and APQC doesn't attempt it.
- **Wrong granularity, in both directions.** A single APQC leaf task ("Screen for sanctions
  compliance") corresponds to a whole cluster of entities in `banking_policy` —
  `AML_SCREENING`, `CUSTOMER`, `REGULATORY_REFERENCE`, `ROLE_RESPONSIBILITY`, plus several
  relationship types (`screens`, `triggers`, `imposes_obligation_on`). Meanwhile APQC has *no*
  node at all for things the schemas need constantly: a `WARNING_SIGN` ("large cash deposit
  followed by immediate transfer"), a `TEMPLATE_VARIABLE`, a `RATE_ENTRY` with a specific £
  threshold, an `ESCALATION_LEVEL` with an approval limit. These aren't "activities" — they're
  facts, thresholds, and artifacts a process taxonomy has no vocabulary for.
- **No temporality or provenance.** Real documents version and supersede each other
  (`effective_date`, `valid_from` in `banking_policy`/`banking_reference`). APQC nodes are
  timeless classification labels — they don't help decide whether "AML Policy v3" superseded
  "AML Policy v2."

### Where the concepts overlap — and why that causes confusion

The danger isn't total mismatch, it's *partial* overlap on the same English word, which
tempts a naive design into collapsing graph nodes that should stay separate.

- **"KYC" appears three times in the schemas, not once**: `KYC_VERIFICATION` in
  `banking_sop_guides` (the executed steps), `COMPLIANCE_CHECKPOINT` in the same schema (the
  gate that verifies it happened), and `POLICY_PROVISION`/`CONTROL_MEASURE` in
  `banking_policy` (the rule that mandates it). Mapping APQC's single "Perform KYC" task 1:1
  onto one entity_class collapses "the rule," "the gate," and "the execution" into one node —
  losing the ability to answer "who verified this customer" separately from "what regulation
  required it."
- **"Risk" means three different things**: `RISK` in `banking_risk_governance` (an
  appetite/tolerance with metrics and current exposure), `RISK_CATEGORY` in `banking_policy`
  (a taxonomy of threat types a policy addresses), and APQC's "Manage Operational Risk" (a
  process activity). Same word, three incompatible entity shapes — appetite-setting
  governance data vs. a classification label vs. a process step.
- **"Audit"**: APQC has a process node "Perform internal audit." `AUDIT_FINDING` is a granular
  instance with `severity`, `resolution_status`, `remediation_deadline`. Using the APQC label
  as the extraction unit produces one blob entity per audit document instead of N findings,
  each with its own remediation chain — the exact information loss a compliance officer would
  actually query for.

General pattern: APQC labels *what is done*; the entity classes capture *what is stated* —
documents, provisions, roles, thresholds, red flags, template variables — many of which APQC
has no concept of at all, and the ones it does name (KYC, risk, audit) don't decompose the
same way once structured facts are being pulled out of prose.

### Where APQC is still useful: a completeness checklist

Used *after* extraction rather than as a design template, APQC earns its keep as an external
coverage audit:

- Walk the Banking PCF's process groups and ask, for each leaf process, "does any of the 7
  `entity_class` vocabularies even have a slot that could hold a fact about this?" A category
  like Treasury/Capital Markets having zero matching entity_class anywhere is a signal —
  either a deliberate scope gap or a missing schema.
- Cross-check the `PROCESS` type values in `banking_sop_guides` (`account_opening`,
  `standing_order_setup`, etc.) against real APQC process names — if a document describes a
  process APQC doesn't recognize in banking at all, that's worth a second look: either it's a
  legitimately bank-specific procedure, or it's a mis-extraction.
- Treat it as a post-hoc matrix (APQC leaf process × "do ingested documents/entities cover
  it?"), not a pre-hoc schema generator. Good for finding blind spots in corpus coverage, bad
  for defining what an entity or relationship should look like.

## FIBO (Financial Industry Business Ontology)

FIBO is EDM Council's formal OWL/RDF ontology for the financial industry, organized into
modules (FND foundations, BE business entities, FBC financial business & commerce, LOAN,
SEC securities, IND indices, etc.), designed for semantic interoperability, description-logic
reasoning, and regulatory data standardization (e.g. linking legal entities to LEIs).

### Why it isn't adequate for KG extraction

- **Wrong tool class entirely.** FIBO is a heavyweight formal ontology built for OWL/DL
  reasoners and RDF triple stores — class hierarchies with necessary/sufficient conditions,
  cardinality restrictions, and object-property domain/range axioms. That's built for
  validating structured data feeds and enabling automated inference, not for guiding an LLM
  prompt to pull facts out of noisy prose. Translating FIBO's formal axioms into
  "what counts as an entity, what's an example, what's out of scope" extraction guidance
  (the job the `entities_prompt` blocks in every `banking_*` schema do) is not something FIBO
  provides — it assumes the data is already structured.
- **Opposite granularity problem from APQC.** Where APQC is process-only and entity-blind,
  FIBO is entity/instrument-only and process-blind. It has no vocabulary for `PROCESS_STEP`,
  `ESCALATION_LEVEL`, `COMPLIANCE_CHECKPOINT`, `WARNING_SIGN`, `TRAINING_MODULE`,
  `AUDIT_FINDING`, `GOVERNANCE_DECISION` — none of the operational/organizational entities
  that `banking_sop_guides`, `banking_risk_governance`, and `banking_communications` are built
  around. FIBO models what a financial instrument or legal entity *is*, not what a bank *does*
  or what an auditor *found*.
- **Formal precision the source documents don't support.** A FIBO `Loan` or `DepositAccount`
  class carries dozens of formally-defined properties (day-count convention, interest
  computation method, legal jurisdiction of formation) intended for precise instrument
  modeling across institutions. A T&Cs document mentioning "SmartSaver Account" rarely states
  enough to responsibly populate that level of formal detail — forcing the mapping either
  produces mostly-empty FIBO instances or invites the extractor to hallucinate values FIBO
  expects but the text never stated (exactly what the schemas' "DO NOT HALLUCINATE" rules
  guard against).

### Where the concepts overlap — and why that causes confusion

- **"Party" / "Account holder."** FIBO's Business Entity module has a rigorous
  `LegalPerson`/`NaturalPerson`/`Organization` subclass hierarchy for formal legal-entity
  typing. `CUSTOMER` and `ACCOUNT_HOLDER` in the schemas are looser, document-instance-driven
  categories (`individual_customer`, `joint_holder`, `pep_customer`). Aligning every mention to
  a FIBO legal-person subtype requires a level of formal detail bank documents rarely state —
  most mentions would either fail to map cleanly or get force-fit into a FIBO subtype the text
  doesn't actually support.
- **"Product" / "Account."** FIBO's FBC module has a strict instrument taxonomy distinguishing
  a security from a loan from a deposit via formal is-a hierarchies tied to regulatory
  classification codes. `PRODUCT` in `banking_products` is retail-marketing-level
  ("SmartSaver Account," "SmartSaver Plus") — mapping that onto a FIBO instrument class is an
  interpretive leap the source document gives no grounds for.
- **"Risk."** FIBO's risk-adjacent modeling is oriented toward market/credit risk tied to
  instruments and counterparties, not the operational/compliance risk taxonomy in
  `RISK_CATEGORY` (money laundering, sanctions violation, data breach). Same word, again two
  incompatible modeling intents — using FIBO's risk classes to validate or constrain
  `RISK_CATEGORY` would quietly drop the compliance-risk concepts FIBO was never built to hold.

### Where FIBO is still useful

- **Canonical reference vocabulary for entity resolution, not extraction design.** Post-
  extraction, FIBO's Business Entity module is a rigorous target taxonomy for reconciling
  `CUSTOMER`/`ACCOUNT_HOLDER` instances against an external identity system (e.g., linking to
  an LEI), or for canonicalizing `PRODUCT` mentions across documents into a standard
  instrument classification. That's an entity-resolution/crosswalk step layered on top of the
  graph, not something that should shape the `entities_prompt`.
- **Interoperability bridge.** If the artmind KG ever needs to exchange data with another
  institution's or regulator's systems that speak FIBO/RDF, a mapping table between
  `entity_class`/`type` values and FIBO classes would let data be exported/aligned without
  redesigning the extraction schemas themselves.
- **Rigor check for financial-instrument and legal-entity distinctions.** Useful as a sanity
  check on the `PRODUCT` taxonomy: does it conflate two things FIBO treats as structurally
  distinct (e.g., a deposit account and a loan)? If so, that's a signal to check whether
  product-specific relationships (interest calculation rules, liability rules) are being
  applied correctly across what should be different product families.

## Summary

| | APQC Banking PCF | FIBO | `banking_*` schemas |
|---|---|---|---|
| Models | Business processes (what is done) | Financial instruments & legal entities (what things are, formally) | Entities + relationships extracted from real documents |
| Granularity | Coarse, label-only | Fine, formal, axiom-driven | Document-instance-driven, prompt-guided |
| Process-level entities (steps, checkpoints, escalations) | Named but not modeled | Absent | First-class (`PROCESS_STEP`, `COMPLIANCE_CHECKPOINT`, `ESCALATION_LEVEL`) |
| Instrument/legal-entity formalism | Absent | First-class | Informal, document-driven (`PRODUCT`, `CUSTOMER`) |
| Best use relative to the schemas | Post-hoc completeness checklist across process categories | Post-hoc entity-resolution/crosswalk target for parties & products | The actual extraction schema |

## Gap analysis: FIBO entities vs. `banking_*` schema coverage

FIBO is organized into modules — FND (Foundations), BE (Business Entities), FBC (Financial
Business and Commerce, including Deposits & Accounts and Products & Services), LOAN, SEC
(Securities), IND (Indices & Indicators), DER (Derivatives), CAE (Corporate Actions). Mapping
each module against the 7 `banking_*` schemas shows FIBO's coverage is concentrated in three
areas — parties/organization, products/accounts, and rates — and essentially absent elsewhere.

### Coverage matrix

| FIBO module / class | Nearest schema entity_class | Coverage | Gap |
|---|---|---|---|
| BE: `LegalPerson`, `NaturalPerson`, `Organization` | `CUSTOMER`, `ACCOUNT_HOLDER` (products, policy, sop_guides, reference, communications) | Partial | FIBO formally distinguishes natural vs. legal persons and organization subtypes with identifiers (LEI); schema's `CUSTOMER` types (`individual_customer`, `pep_customer`, `high_risk_customer`) are risk/servicing labels, not legal-form classifications. Schema has no `legal_form` or `identifier_scheme` property at all. |
| FND-ORG: `FormalOrganization`, `OrganizationalUnit` | `ORGANIZATIONAL_UNIT` (organization) | Partial | FIBO models formal org hierarchy (incorporation, legal jurisdiction) as a legal-entity concept; schema's `ORGANIZATIONAL_UNIT` is purely descriptive (`mission`, `headcount`, `reporting_to`) with no legal-entity grounding — reasonable for internal org charts, but means the two can't be joined without an identifier bridge. |
| FBC-DAE: `DepositAccount`, `CurrentAccount`, `SavingsAccount`, `TermDeposit` | `ACCOUNT`, `PRODUCT` (products, policy, sop_guides, reference) | Partial | FIBO subclasses accounts formally by deposit mechanics (demand vs. term, withdrawal restrictions); schema's `account_type` values (`savings_account`, `current_account`, `joint_account`) are string labels on a flat `ACCOUNT`/`PRODUCT` entity with no formal is-a hierarchy — fine for extraction, but two differently-worded product names can't be recognized as "the same FIBO class" without a mapping step. |
| FBC-DAE: `InterestRate`, `RateBasis` | `INTEREST_RATE_TIER` (products), `RATE_ENTRY` (reference) | Good overlap | This is the closest match in the whole ontology — both model rate value, basis, and effective period. Gap is mostly one-directional: FIBO has no notion of a *tiered* rate keyed to a balance range (`balance_min`/`balance_max`), which the schema had to add itself since UK retail tiered-savings products aren't a first-class FIBO pattern. |
| FBC-PAS: `FinancialProduct`, `FinancialService` | `PRODUCT` (products, sop_guides, reference, communications) | Partial | FIBO's `FinancialProduct` is a thin, formal umbrella class primarily used to link a product instance to its issuing organization and instrument type; it carries none of the schema's retail-facing detail (`monthly_fee`, `overdraft_available`, `available_channels`) — that detail simply has no home in FIBO. |
| FBC-FI: `FinancialInstrument` (and SEC/LOAN subclasses: `Security`, `Bond`, `Loan`, `Mortgage`) | `PRODUCT` (products), `CARD` (multiple schemas) | Weak | The schemas cover retail deposit products and payment cards almost exclusively — FIBO's real depth here is in securities, derivatives, and lending instruments, none of which appear in the corpus. A `CARD` entity has no FIBO analogue at all (cards sit closer to payment-scheme standards than to FIBO). |
| FND-AGR: `Agreement`, `Contract` | `TERMS_CLAUSE` (products) | Partial | FIBO models an `Agreement` as a formal instrument between parties with obligations/rights as first-class objects; `TERMS_CLAUSE` captures the same intent (`customer_rights`, `bank_obligations`) but as a flat property bag on a document-derived entity rather than a linked formal contract object — adequate for Q&A, not for contract-management use cases. |
| FND-PLC: `PostalAddress` | `ADDRESS` (sop_guides, reference) | Good overlap | Close conceptual match; FIBO's address model is more formally decomposed (structured sub-fields) than the schema's process-oriented `ADDRESS` (`collection_steps`, `verification_steps`), which is appropriately extraction-focused rather than structurally exhaustive. |
| FND-ACC: `MonetaryAmount`, `Currency` | Implicit in `FEE`, `RISK_METRIC`, `RATE_ENTRY` properties | Partial | FIBO treats currency-denominated amounts as typed objects; the schemas store amounts as plain strings in properties (`"£500–£5,000"`). Fine for RAG/Q&A, but means no schema currently supports currency-aware numeric comparison or conversion. |
| *(no FIBO module)* | `POLICY`, `POLICY_PROVISION`, `CONTROL_MEASURE`, `KYC_VERIFICATION`, `AML_SCREENING`, `SAR`, `FRAUD_ALERT` (policy, sop_guides, risk_governance, communications) | **None** | FIBO has no AML/KYC/sanctions/fraud/financial-crime module in mainstream releases — this is the single largest gap. Everything in `banking_policy` and most of `banking_risk_governance` has zero FIBO grounding. |
| *(no FIBO module)* | `PROCESS`, `PROCESS_STEP`, `DECISION_POINT`, `ESCALATION_LEVEL`, `COMPLIANCE_CHECKPOINT` (sop_guides) | **None** | FIBO is not a process ontology (see APQC section above for that gap) — no overlap here at all. |
| *(no FIBO module)* | `AUDIT_FINDING`, `GOVERNANCE_DECISION`, `ACTION_ITEM`, `RISK_METRIC`, `REGULATORY_UPDATE` (risk_governance) | **None** | Internal audit and board-governance artifacts are out of FIBO's scope entirely. |
| *(no FIBO module)* | `COMMUNICATION_TEMPLATE`, `TEMPLATE_VARIABLE`, `TRAINING_MODULE`, `LEARNING_OBJECTIVE`, `WARNING_SIGN`, `COMPLIANCE_OBLIGATION` (communications) | **None** | Staff training and customer-comms artifacts have no analogue anywhere in FIBO. |
| *(no FIBO module)* | `INCIDENT_TYPE`, `SEVERITY_LEVEL`, `RESPONSE_ACTION`, `NOTIFICATION_CONTACT`, `CUSTOMER_GUIDE_ITEM` (reference) | **None** | Incident response and welcome-pack guidance are operational reference material, not financial-instrument or legal-entity concepts — no FIBO coverage. |

### Reading the gap

Of the roughly 60 distinct entity classes across the 7 schemas, only about 8 (`CUSTOMER`,
`ACCOUNT_HOLDER`, `ORGANIZATIONAL_UNIT`, `ACCOUNT`, `PRODUCT`, `INTEREST_RATE_TIER`/
`RATE_ENTRY`, `TERMS_CLAUSE`, `ADDRESS`) have any meaningful FIBO counterpart, and even those
are partial — FIBO supplies formal legal/instrument structure the schemas don't need for
document extraction, while the schemas supply retail operational detail FIBO was never built
to hold. The other roughly 50 entity classes — everything in `banking_policy`,
`banking_risk_governance`, `banking_communications`, and most of `banking_sop_guides` and
`banking_reference` — sit entirely outside FIBO's scope, because FIBO models financial
instruments, legal entities, and corporate actions, not AML/KYC compliance, internal audit,
staff training, or incident response.

### What this means in practice

- **Don't use FIBO as a completeness check the way APQC is used above.** APQC's process
  taxonomy at least nominally spans the whole bank; FIBO's silence on compliance/operations/
  training isn't a coverage gap in the corpus, it's a scope boundary of the ontology itself. A
  "0% FIBO coverage" result for `banking_policy` doesn't indicate a missing schema.
- **The one place a FIBO crosswalk would pay off is `PRODUCT`/`ACCOUNT`/`CUSTOMER`.** If the KG
  ever needs to interoperate with another system that speaks FIBO/RDF (a core banking platform,
  a regulator submission), that's the narrow slice — product and account typing, party legal
  form — worth adding a `fibo_class` cross-reference property to, rather than reshaping the
  extraction prompts themselves.
- **`INTEREST_RATE_TIER`/`RATE_ENTRY` is the best-aligned pair in the whole comparison** and the
  most likely candidate for a light formalization (e.g., typed `rate_basis`, `currency`) if the
  graph ever needs to support numeric rate comparisons across products rather than just RAG
  retrieval.
