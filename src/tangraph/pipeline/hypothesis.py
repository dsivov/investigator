"""Domain → canned hypothesis prompt strings.

These strings feed the `hypothesis` input of the evidence-extraction signatures
(`ExtractEvidenceFromJSONText` / `InvestigateEvidenceFromJSONText`) at runtime.
Treat changes here as prompt engineering, not refactor mechanics.
Phase 3 candidate: move the strings to a config file (yaml/toml) so non-
engineers can iterate on them without code changes.
"""


def return_hypothesis_for_domain(domain: str) -> str:
    if domain == "terror_financing":
        return (
            "Domain: Terror Financing\n"
            "Assess whether Entity has credible associations with "
            "terror-affiliated organizations or individuals, and evaluate the strength of any financial or "
            "operational connections that could indicate involvement in terror financing. "
            "If any specific sanctions codes are mentioned in the evidence, explicitly reference them and "
            "provide links if available. "
            "Provide all the links that are mentioned in the text. Do not omit any."
            "After generating your assessment, validate that all required output fields are complete and that any "
            "claims about evidence or codes are clearly backed by the summary. "
            "If no evidence or sanctions codes are identified, explicitly state this in the respective fields."
        )
    if domain == "narcotics":
        return """
        Domain: Narcotics Trafficking
        Provide a comprehensive analysis on Entity regarding of any known connections to narcotics trafficking or illegal drug operations, including:
        1. **Sanctions Designations**: List all sanctions from EU, US (OFAC), UN, and other jurisdictions where the entity or individual is designated for involvement in narcotics trafficking or drug-related offenses. Include designation dates, issuing authority, and specific charges or reasons cited.
        2. **Criminal Records & Legal Proceedings**: Document any criminal convictions, indictments, arrests, or ongoing legal cases related to narcotics trafficking, drug manufacturing, distribution, or possession with intent to distribute. Include jurisdiction, case numbers, dates, and outcomes.
        3. **Affiliations & Associations**: Identify connections to known drug trafficking organizations, cartels, or individuals sanctioned or convicted for narcotics-related crimes. Specify the nature of the relationship (business partner, family member, associate, etc.) and supporting evidence.
        4. **Geographic Indicators**: Note operations in regions known for drug production or trafficking routes (e.g., Golden Triangle, Golden Crescent, major trafficking corridors).
        5. **Financial Red Flags**: Document suspicious financial activities potentially linked to drug proceeds, including unexplained wealth, money laundering indicators, or transactions with known narcotics-related entities.
        6. **Public Records & Media**: Summarize credible news reports, investigative journalism, or government reports linking the entity or individual to narcotics activities.
        7. **Corporate Structures**: Identify shell companies, front organizations, or legitimate businesses suspected of facilitating drug trafficking operations.
        8. **Temporal Information**: Provide timeline of alleged or confirmed involvement, including start dates, peak activity periods, and any cessation or ongoing status.

        For each finding, cite specific sources, dates, and confidence levels.
        Clearly distinguish between confirmed designations, criminal convictions, and allegations under investigation.
        Present only facts and do not add data that does not exists in the sources that you fetched data from
        """
    if domain == "edd":
        return """
        Domain: Enhanced Due Diligence (EDD)
        Assess whether Entity has credible associations with one or more of the following:"
        - Official sanctions by any jurisdiction (EU, US/OFAC, UN, etc.)
        - Criminal convictions, indictments, arrests, or pending legal actions
        - Documented associations with individuals or organizations convicted or sanctioned
        - Presence or operational activity in restricted geographic areas
        - Suspicious financial conduct indicative of narcotics proceeds, such as unexplained wealth or money laundering
        - Credible references in public records, reputable media, or official reports
        - Corporate or ownership structures suspected of facilitating illegal activity
        - Any timeline evidencing alleged or confirmed involvement in illegal activity

        For each finding, cite specific sources, dates, and confidence levels.
        Clearly distinguish between confirmed designations, criminal convictions, and allegations under investigation.
        Present only facts and do not add data that does not exists in the sources that you fetched data from
        """
    return f"Provide a comprehensive analysis on Entity regarding potential risks and associations relevant to the {domain} domain."
