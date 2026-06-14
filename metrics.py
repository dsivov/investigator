from investigator.graph.similarity import jaccard_similarity_edges, jaccard_similarity_nodes, cosine_similarity_nodes, cosine_similarity_edges
from typing import List
import json
from wordllama import WordLlama
def evaluate_entities(gt_nodes: List[dict], result: dict):
    print("----- Entities Evaluation -----")    
    print("First stage: Identifiers only comparison")
    print(f"Result length: {len(result['nodes'])}")
    print(f"GT data length: {len(gt_nodes)}")
    gt_identifiers = [node['identifier'].upper() for node in gt_nodes]
    result_identifiers = [node['identifier'].upper() for node in result['nodes']]
    #result_identifiers =  []
    # for node in result['nodes']:
    #     if 'data' in node:
    #         if 'name' in node['data']:
    #             if isinstance(node['data']['name'], str):
    #                 result_identifiers.append(node['data']['name'].upper())
    #             elif isinstance(node['data']['name'], list):
    #                 for name in node['data']['name']:
    #                     if name.upper() not in result_identifiers:
    #                         result_identifiers.append(name.upper())
   
    print("----- Entities Evaluation Results -----")

    print(f"Ground truth identifiers count: {len(gt_identifiers)}")
    print(f"Result identifiers count: {len(result_identifiers)}")

    print("----- Detailed Metrics -----")
    common_identifiers = set(gt_identifiers).intersection(set(result_identifiers))
    union_identifiers = set(gt_identifiers).union(set(result_identifiers))
    not_found_in_result = set(gt_identifiers) - set(result_identifiers)
    not_in_ground_truth = set(result_identifiers) - set(gt_identifiers)
    # for identifier in not_found_in_result:
    #     print(f"Not found in result: {identifier}")
    # for identifier in not_in_ground_truth:
    #     print(f"Not in ground truth: {identifier}")
    print(f"Not in ground truth count: {len(not_in_ground_truth)}")
    print(f"Not found in result count: {len(not_found_in_result)}")      
    print(f"Intersected identifiers count: {len(common_identifiers)}")
    print(f"Union identifiers count: {len(union_identifiers)}")
    print("Precision:", len(common_identifiers) / len(result_identifiers) if len(result_identifiers) > 0 else 0)
    print("Recall:", len(common_identifiers) / len(gt_identifiers) if len(gt_identifiers) > 0 else 0)
    print("F1 Score:", 2 * (len(common_identifiers) / len(result_identifiers) * len(common_identifiers) / len(gt_identifiers)) / (len(common_identifiers) / len(result_identifiers) + len(common_identifiers) / len(gt_identifiers)) if (len(result_identifiers) > 0 and len(gt_identifiers) > 0 and (len(common_identifiers) > 0)) else 0)
    print("Intersection over union ratio:", len(common_identifiers) / len(union_identifiers) if len(union_identifiers) > 0 else 0)
    
    print("Number of unique identifiers in ground truth:", len(set(gt_identifiers)))
    print("Number of unique identifiers in result:", len(set(result_identifiers)))
    
    print("ground truth:", json.dumps(sorted(gt_identifiers), indent=4))
    print("result:", json.dumps(sorted(result_identifiers), indent=4))

    print("Not found in result identifiers:", json.dumps(sorted(list(not_found_in_result)), indent=4))
    print("Not in ground truth identifiers:", json.dumps(sorted(list(not_in_ground_truth)), indent=4))
    nodes_to_compare = []
    for intersected_id in common_identifiers:
        for resolved_node in result['nodes']:
            if intersected_id == resolved_node['identifier'].upper():
                nodes_to_compare.append(resolved_node)

    jaccard_similarity_score = jaccard_similarity_nodes(gt_nodes, nodes_to_compare)
    print(f"Jaccard Similarity Score for common entities: {jaccard_similarity_score}")
    cosine_similarity_score = cosine_similarity_nodes(gt_nodes, nodes_to_compare)
    print(f"Cosine Similarity Score for common entities: {cosine_similarity_score}")

    print("Second stage: Token-level comparison")
    all_resolved_tokens = "".join(result_identifiers).split()
    all_gt_tokens = "".join(gt_identifiers).split()
    common_tokens = set(all_gt_tokens).intersection(set(all_resolved_tokens))
    #print(f"Intersected tokens between ground truth and result identifiers: {common_tokens}")
    union_tokens = set(all_gt_tokens).union(set(all_resolved_tokens))
    #print(f"Union tokens between ground truth and result identifiers: {union_tokens}")
    print(f"Intersected tokens count: {len(common_tokens)}")
    print(f"Union tokens count: {len(union_tokens)}")
    print("Token-level Precision:", len(common_tokens) / len(all_resolved_tokens) if len(all_resolved_tokens) > 0 else 0)
    print("Token-level Recall:", len(common_tokens) / len(all_gt_tokens) if len(all_gt_tokens) > 0 else 0)
    print("Token-level F1 Score:", 2 * (len(common_tokens) / len(all_resolved_tokens) * len(common_tokens) / len(all_gt_tokens)) / (len(common_tokens) / len(all_resolved_tokens) + len(common_tokens) / len(all_gt_tokens)) if (len(all_resolved_tokens) > 0 and len(all_gt_tokens) > 0 and (len(common_tokens) > 0)) else 0)
    print("Token-level Intersection over union ratio:", len(common_tokens) / len(union_tokens) if len(union_tokens) > 0 else 0)

def evaluate_edges(gt_edges: List[dict], result: dict):
    print("----- Edges Evaluation -----")
    print("First stage: Identifiers only comparison")
    print(f"Result length: {len(result['edges'])}")
    print(f"GT data length: {len(gt_edges)}")
    # gt_identifiers_original_order = [edge['src_identifier'].upper() + ":" + edge['dst_identifier'].upper() for edge in gt_edges]
    # gt_identifiers_reverse_order = [edge['dst_identifier'].upper() + ":" + edge['src_identifier'].upper() for edge in gt_edges]
    gt_identifiers = [edge['src_identifier'].upper() + ":" + edge['dst_identifier'].upper() for edge in gt_edges]
    result_identifiers_original_order = [edge['src_identifier'].upper() + ":" + edge['dst_identifier'].upper() for edge in result['edges']]
    result_identifiers_reverse_order = [edge['dst_identifier'].upper() + ":" + edge['src_identifier'].upper() for edge in result['edges']]
    result_identifiers = result_identifiers_original_order + result_identifiers_reverse_order
    print("----- Edges Evaluation Results -----")
    print(f"Ground truth identifiers count: {len(gt_identifiers)}")
    print(f"Result identifiers count: {len(gt_edges)}")
    common_identifiers = set(gt_identifiers).intersection(set(result_identifiers))
    union_identifiers = set(gt_identifiers).union(set(result_identifiers))
    not_found_in_result = set(gt_identifiers) - set(result_identifiers)
    not_in_ground_truth = set(result_identifiers) - set(gt_identifiers)
    # for identifier in not_found_in_result:
    #     print(f"Not found in result: {identifier}")
    # for identifier in not_in_ground_truth:
    #     print(f"Not in ground truth: {identifier}")     
    print(f"Not in ground truth count: {len(not_in_ground_truth)}")
    print(f"Not found in result count: {len(not_found_in_result)}")      
    print(f"Intersected edges count: {len(common_identifiers)}")
    print(f"Union edges count: {len(union_identifiers)}")
    print("Precision:", len(common_identifiers) / len(result_identifiers_original_order) if len(result_identifiers_original_order) > 0 else 0)
    print("Recall:", len(common_identifiers) / len(gt_identifiers) if len(gt_identifiers) > 0 else 0)
    print("F1 Score:", 2 * (len(common_identifiers) / len(result_identifiers_original_order) * len(common_identifiers) / len(gt_identifiers)) \
          / (len(common_identifiers) / len(result_identifiers_original_order) + len(common_identifiers) / len(gt_identifiers)) \
              if (len(result_identifiers_original_order) > 0 and len(gt_identifiers) > 0 and (len(common_identifiers) > 0)) else 0)
    print("Intersection over union ratio:", len(common_identifiers) / len(union_identifiers) if len(union_identifiers) > 0 else 0)


    # common_identifiers = set(gt_identifiers_original_order).intersection(set(result_identifiers_original_order))
    # union_identifiers = set(gt_identifiers_original_order).union(set(result_identifiers_original_order))
    # not_found_in_result = set(gt_identifiers_original_order) - set(result_identifiers_original_order)
    # not_in_ground_truth = set(result_identifiers_original_order) - set(gt_identifiers_original_order)
    # for identifier in not_found_in_result:
    #     print(f"Not found in result: {identifier}")
    # for identifier in not_in_ground_truth:
    #     print(f"Not in ground truth: {identifier}")     
    # print(f"Not in ground truth count: {len(not_in_ground_truth)}")
    # print(f"Not found in result count: {len(not_found_in_result)}")      
    # print(f"Intersected edges count: {len(common_identifiers)}")
    # print(f"Union edges count: {len(union_identifiers)}")
    # print("Precision:", len(common_identifiers) / len(result_identifiers_original_order) if len(result_identifiers_original_order) > 0 else 0)
    # print("Recall:", len(common_identifiers) / len(gt_identifiers_original_order) if len(gt_identifiers_original_order) > 0 else 0)
    # print("F1 Score:", 2 * (len(common_identifiers) / len(result_identifiers_original_order) * len(common_identifiers) / len(gt_identifiers_original_order)) / (len(common_identifiers) / len(result_identifiers_original_order) + len(common_identifiers) / len(gt_identifiers_original_order)) if (len(result_identifiers_original_order) > 0 and len(gt_identifiers_original_order) > 0 and (len(common_identifiers) > 0)) else 0)
    # print("Intersection over union ratio:", len(common_identifiers) / len(union_identifiers) if len(union_identifiers) > 0 else 0)
    edges_to_compare = []
    for intersected_id in common_identifiers:
        for resolved_node in result['edges']:
            if intersected_id == resolved_node['src_identifier'].upper() + ":" + resolved_node['dst_identifier'].upper():
                edges_to_compare.append(resolved_node)
    # print(edges_to_compare)
    jaccard_similarity_score = jaccard_similarity_edges(gt_edges, edges_to_compare)
    print(f"Jaccard Similarity Score for common entities: {jaccard_similarity_score}")
    cosine_similarity_score = cosine_similarity_edges(gt_edges, edges_to_compare)
    print(f"Cosine Similarity Score for common entities: {cosine_similarity_score}")

def compare_entities(gt_nodes: List[str], result_nodes: List[str], wl: WordLlama):
    
    for query in gt_nodes:
        top_docs = wl.topk(query, result_nodes, k=1)
        if query != top_docs[0]:
            print(f"Current: {query}: Server: {top_docs[0]}")

def main():
    wl = WordLlama.load()
    current_flow_identifiers = [
    "ABUNDANT PROVISIONS LLC",
    "AIDAH ABDALLAH",
    "AIRDROP INTO GAZA STRIP",
    "AL-KHIDMAT FOUNDATION",
    "AL-QAEDA",
    "AMERICAN MUSLIMS FOR PALESTINE (AMP)",
    "AMERICAN NEAR EAST REFUGEE AID (ANERA)",
    "AMERICAN RELIEF AGENCY FOR THE HORN OF AFRICA",
    "AMINA DEMIR",
    "AMNA MIRZA",
    "APPNA",
    "ARISE CHICAGO",
    "GLOBALAID",
    "BAM AGENCY LLC",
    "BENEVOLENCE INTERNATIONAL FOUNDATION",
    "BREAD FOR THE WORLD",
    "BRUSH ARCHITECTS LLC",
    "CAIR-CHICAGO",
    "CATHOLIC ARCHDIOCESE OF CHICAGO",
    "CATHOLIC RELIEF SERVICES",
    "CENTER FOR ISLAMOPHOBIA STUDIES",
    "CENTER ON MUSLIM PHILANTHROPY",
    "CHARITY & SECURITY NETWORK",
    "CLARK NUBER",
    "COUNCIL ON AMERICAN-ISLAMIC RELATIONS (CAIR)",
    "CRITERION CONCEPTS LLC",
    "DAR AL HIJRAH ISLAMIC CENTER",
    "DARAJAT CO",
    "DHAKA RESTAURANT INC",
    "DINA KARMI",
    "DONNA NEIL-DEMIR",
    "DOORDASH",
    "DR MEHMET TARHAN",
    "ELEVATED ECHELON LLC",
    "ENAAM ARNAOUT",
    "FATIMA KHALIL",
    "FATIMA TUNCER",
    "FRANCES OLSON",
    "FUAT YAZAR",
    "GEI CONSULTANTS",
    "GLOBAL HEALTH COUNCIL",
    "GREATER EVENNESS LLC",
    "JOHN DOE",
    "HAMAS",
    "HASAN ARSLAN",
    "HELPING HAND FOR RELIEF AND DEVELOPMENT",
    "HELPING HAND FOR RELIEF AND DEVELOPMENT (HHRD)",
    "HOLY LAND FOUNDATION (HLF)",
    "HUMANITY HOSPITAL AND MEDICAL CENTER LAHORE",
    "INDIAN SIZZLER",
    "INTERACTION",
    "INTERACTION TOGETHER PROJECT",
    "INTERACTION TOGETHER PROJECT (RECENTLY RENAMED CIVIC SPACE)",
    "INTERNATIONAL GOLDEN FOODS",
    "ISLAMIC CENTER OF NORTH AMERICA NY",
    "ISLAMIC CENTER OF WHEATON",
    "ISLAMIC CHARITABLE SOCIETY (HEBRON)",
    "ISLAMIC CHARITABLE SOCIETY (WEST BANK)",
    "ISLAMIC COMMUNITY CENTER OF ILLINOIS",
    "ISLAMIC RELIEF USA",
    "ISLAMIC SOCIETY OF ORANGE COUNTY",
    "JAMAL SAID",
    "JAMEA-TUS-SALEHAT SCHOOL PROJECT",
    "JENNIFER BECKER HARRIS",
    "JORDAN HASHEMITE CHARITY ORGANIZATION",
    "KHALIL CENTER",
    "KJOHN DOE",
    "KHAN BABA",
    "LIFE FOR RELIEF AND DEVELOPMENT",
    "LINGO LOGIC",
    "LOCAL PARTNER ORGANIZATIONS",
    "MAS CONVENTION CHICAGO",
    "MAS CONVENTION GLA",
    "MECCA CENTER",
    "MEMBERS OF TURKISH PRESIDENT ERDO\u011eAN'S FAMILY",
    "MIGRANT SHELTER (FORMER ST. BARTHOLOMEW SCHOOL BUILDING, CHICAGO)",
    "MINARA EL-RAHMAN",
    "MOSQUE FOUNDATION",
    "MUELLER & CO LLP",
    "MUSLIM CIVIC COALITION",
    "MUSLIM LEGAL FUND OF AMERICA",
    "MUSLIM WOMEN RESOURCE CENTER",
    "NASHAAT AL-KARMI",
    "NEIU FOUNDATION",
    "NEO FOUNDATION",
    "NEO PHILANTHROPY",
    "OFFICE OF U.S. REP. ILHAN OMAR (D-MN)",
    "OUT-OF-JURISDICTION COMPANY",
    "PROTEUS FUND",
    "RARANGA CONSTRUCTION INC",
    "RATHS RATHS & JOHNSON INC",
    "RAZA FARRUKH",
    "RED CRESCENT OF AMERICA",
    "ROHINGYA CULTURAL CENTER",
    "RONALD MCDONALD HOUSE",
    "ROYAL JORDANIAN AIR FORCE",
    "SAOUSSEN HABALI",
    "SEED TO MOUNTAIN LLC",
    "SELMA DEMIR",
    "SEVERAL METHODIST CHURCHES",
    "SHEIKH MAULANA MOHAMMAD YUSUF ISLAHI",
    "SYRIA RELIEF & DEVELOPMENT",
    "SYRIAN AMERICAN MEDICAL SOCIETY (SAMS)",
    "THE DOWNTOWN CLUSTER OF CONGREGATIONS",
    "THE FAMILY & YOUTH INSTITUTE",
    "THE MOKHA INSTITUTE",
    "THE PRAYER CENTER",
    "THE SALAAM COMMUNITY WELLNESS CENTER",
    "TIDES FOUNDATION",
    "TRAVELERS MEDIA LLC",
    "TURKEN FOUNDATION",
    "TURKISH HUMANITARIAN RELIEF ORGANIZATION (IHH)",
    "UC BERKELEY FOUNDATION",
    "UNICEF USA",
    "UNITED MUSLIMS RELIEF",
    "UNITED NATIONS CHILDRENS FUND (UNICEF)",
    "UNIVERSAL SCHOOL",
    "UNLIMITED FRIENDS ASSOCIATION FOR SOCIAL DEVELOPMENT (UFA)",
    "UNRWA",
    "UPWARDLY GLOBAL",
    "US COUNCIL OF MUSLIM ORGANIZATIONS",
    "WEST COAST ISLAMIC SOCIETY",
    "ZAHRAA UNIVERSITY (TURKEY)",
    "ACME FOUNDATION OF AMERICA"]

    server_flow_identifiers = [
    "ABUNDANT PROVISIONS LLC",
    "AIDAH ABDALLAH",
    "AISHA AZHAR",
    "AL RAHMA SCHOOL",
    "AL-KHIDMAT FOUNDATION",
    "AMERICAN MUSLIMS FOR PALESTINE (AMP)",
    "AMINA DEMIR",
    "AMNA MIRZA",
    "APPNA",
    "ARISE CHICAGO",
    "BABA KHAN",
    "GLOBALAID",
    "BAM AGENCY LLC",
    "BENEVOLENCE INTERNATIONAL FOUNDATION",
    "BRUSH ARCHITECTS LLC",
    "CAIR-CHICAGO",
    "CATHOLIC ARCHDIOCESE OF CHICAGO",
    "CENTER FOR ISLAMOPHOBIA STUDIES",
    "CENTER ON MUSLIM PHILANTHROPY",
    "COUNCIL ON AMERICAN-ISLAMIC RELATIONS (CAIR)",
    "CRITERION CONCEPTS LLC",
    "DAR AL HIJRAH ISLAMIC CENTER",
    "DARAJAT CO",
    "DHAKA RESTAURANT INC",
    "DINA KARMI",
    "DONNA NEIL-DEMIR",
    "DOORDASH",
    "DR HASAN ARSLAN",
    "DR MEHMET TARHAN",
    "ELEVATED ECHELON LLC",
    "ENAAM ARNAOUT",
    "FATIMA KHALIL",
    "FATIMA TUNCER",
    "FUAT YAZAR",
    "GEI CONSULTANTS",
    "GORHAM UNITED METHODIST CHURCH",
    "GREATER EVENNESS LLC",
    "GULSIN",
    "HALIL (KHALIL) DEMIR",
    "HELPING HAND FOR RELIEF AND DEVELOPMENT",
    "HIZBUL MUJAHIDEEN",
    "HOLY LAND FOUNDATION FOR RELIEF AND DEVELOPMENT (HLF)",
    "HOOMAN KESHAVARZI",
    "HUMANITARIAN RELIEF FOUNDATION (IHH)",
    "HUMANITY HOSPITAL AND MEDICAL CENTER LAHORE",
    "ICS",
    "ILLINOIS MUSLIM CIVIC COALITION",
    "INDIAN SIZZLER",
    "INDIANA UNIVERSITY LILLY FAMILY SCHOOL OF PHILANTHROPY",
    "INTERACTION",
    "INTERACTIONS TOGETHER PROJECT",
    "INTERNATIONAL GOLDEN FOODS",
    "ISLAMIC CENTER OF NORTH AMERICA NY",
    "ISLAMIC CENTER OF WHEATON",
    "ISLAMIC CHARITABLE SOCIETY IN HEBRON",
    "ISLAMIC COMMUNITY CENTER OF ILLINOIS",
    "ISLAMIC RELIEF WORLDWIDE",
    "ISLAMIC SOCIETY OF NORTH AMERICA",
    "ISLAMIC SOCIETY OF ORANGE COUNTY",
    "JAMAAT-E-ISLAMI (JI)",
    "JAMEA-TUS-SALEHAT SCHOOL PROJECT",
    "JEHANZEB R. CHEEMA",
    "JORDAN HASHEMITE CHARITY ORGANIZATION",
    "KHALIL CENTER",
    "KHALIL FOUNDATION",
    "LINGO LOGIC",
    "MAS CONVENTION CHICAGO",
    "MAS CONVENTION DC",
    "MAS CONVENTION GLA",
    "MD MAHMUDUL HAQUE",
    "MECCA CENTER",
    "MEHNAZ GUL",
    "MINARA EL-RAHMAN",
    "MOSQUE FOUNDATION",
    "MUSLIM LEGAL FUND OF AMERICA",
    "MUSLIM WOMEN RESOURCE CENTER",
    "NASHAAT AL-KARMI",
    "NEIU FOUNDATION",
    "NEO PHILANTHROPY",
    "OFFICE OF U.S. REP. ILHAN OMAR (D-MN)",
    "OSAMA BIN LADEN",
    "PROTEUS FUND",
    "RARANGA CONSTRUCTION INC",
    "RATHS RATHS & JOHNSON INC",
    "RAZA FARRUKH",
    "RED CRESCENT OF AMERICA",
    "ROHINGYA CULTURAL CENTER",
    "RONALD MCDONALD HOUSE",
    "ROYAL JORDANIAN AIR FORCE",
    "SAOUSSEN HABALI",
    "SEED TO MOUNTAIN LLC",
    "SELMA DEMIR",
    "SELMAN KESGIN",
    "SHEIKH HASAN HAJMOHAMMAD",
    "SHEIKH MAULANA MOHAMMAD YUSUF ISLAHI",
    "ST. SABINA PARISH",
    "SUMRIN KALIA",
    "THE FAMILY & YOUTH INSTITUTE",
    "THE MOKHA INSTITUTE",
    "THE PRAYER CENTER",
    "THE SALAAM COMMUNITY WELLNESS CENTER",
    "THE ACME FOUNDATION OF AMERICA",
    "TIDES FOUNDATION",
    "TRAVELERS MEDIA LLC",
    "TURKISH HUMANITARIAN RELIEF ORGANIZATION (IHH)",
    "UC BERKELEY FOUNDATION",
    "UFA",
    "UNITED NATIONS CHILDRENS FUND (UNICEF)",
    "UNITED NATIONS RELIEF AND WORKS AGENCY (UNRWA)",
    "UNIVERSAL SCHOOL",
    "UNLIMITED FRIENDS ASSOCIATION FOR SOCIAL DEVELOPMENT",
    "UPWARDLY GLOBAL",
    "US COUNCIL OF MUSLIM ORGANIZATIONS",
    "WEST COAST ISLAMIC SOCIETY",
    "ZAHRA UNIVERSITY",
    "ZAINAB FARHAT"
    ]
    compare_entities(current_flow_identifiers, server_flow_identifiers, wl)


if __name__ == "__main__":
    main()