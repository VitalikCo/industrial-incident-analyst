"""Create the first 30 grounded, source-excluded query seeds for the CSB golden set.

This prepares query scenarios only. It deliberately does NOT invent relevance labels,
reference causes, or recommendation answers. Those are added after pooling retrieval
results and human annotation.

Run from the repository root:
    uv run python scripts/prepare_golden_set.py
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/processed"
GOLDEN = ROOT / "data/golden"

# Query text is anonymized and paraphrased from the cited CSB executive-summary/abstract
# page. Company names, report titles, dates and source incident IDs stay in hidden metadata.
# The scenario avoids stating the CSB's root-cause conclusions where possible.
SEEDS: list[dict[str, Any]] = [
    {
        "query_id": "Q001", "document_id": "CSB-6220", "page": 7, "section": "Executive Summary",
        "query_text": "At a specialty-chemicals facility, operators were carrying out a batch operation that required manually charging and mixing raw materials in an indoor tank. The contents rapidly foamed and overflowed, an indoor gas release followed, and an explosion and fire occurred. Find historical incidents with comparable batch-chemistry, process-vessel upset, gas-release, and ignition hazards.",
        "facets": {"substance_or_hazard": ["reactive chemicals", "flammable gas", "explosion/fire"], "equipment_or_process": ["batch process", "mixing tank"], "failure_mechanism": ["uncontrolled chemical reaction", "loss of containment", "ignition"], "event_sequence": ["manual charging", "process upset", "overflow/gas release", "fire/explosion"]},
    },
    {
        "query_id": "Q002", "document_id": "CSB-6089", "page": 9, "section": "Executive Summary",
        "query_text": "During restart of a pesticide/intermediate manufacturing unit after an extended outage and equipment changes, pressure rose in a residue-treatment pressure vessel. The vessel ruptured, releasing flammable liquid that ignited and caused a prolonged unit fire. Find comparable cases involving process-vessel overpressure, abnormal startup, reactive chemistry, and flammable releases.",
        "facets": {"substance_or_hazard": ["flammable solvent", "reactive process"], "equipment_or_process": ["pressure vessel", "residue-treatment unit", "unit restart"], "failure_mechanism": ["runaway reaction/overpressure", "vessel rupture"], "event_sequence": ["restart after outage", "rising pressure", "rupture", "ignition/fire"]},
    },
    {
        "query_id": "Q003", "document_id": "CSB-5605", "page": 5, "section": "Executive Summary",
        "query_text": "At a steel mill, workers were performing removal work on a valve and blind in a coke-oven-gas line associated with a decommissioned furnace. A flammable liquid was released during the work and ignited, causing fatalities and serious injuries. Find historical cases involving hazardous energy or process-line isolation, maintenance, and ignition during opening of equipment.",
        "facets": {"substance_or_hazard": ["coke-oven gas", "flammable liquid/vapor"], "equipment_or_process": ["gas piping", "valve", "slip blind", "decommissioned furnace"], "failure_mechanism": ["loss of containment during maintenance", "ignition"], "event_sequence": ["line opening/removal work", "flammable release", "fire"]},
    },
    {
        "query_id": "Q004", "document_id": "CSB-6339", "page": 7, "section": "Executive Summary",
        "query_text": "At a warehouse storing chemicals used in pool and spa treatment, a reaction developed inside the storage area and produced substantial heat and off-gassing. Multiple fires followed and a large toxic smoke plume containing chlorine compounds and bromine affected the surrounding community, prompting evacuations and shelter-in-place actions. Find comparable chemical-storage, incompatibility, decomposition, and toxic-smoke incidents.",
        "facets": {"substance_or_hazard": ["chlorinated chemicals", "bromine", "toxic smoke", "thermal decomposition"], "equipment_or_process": ["chemical storage warehouse"], "failure_mechanism": ["chemical reaction/decomposition", "escalation across stored materials"], "event_sequence": ["warehouse reaction", "heat/off-gassing", "multiple fires", "toxic plume"]},
    },
    {
        "query_id": "Q005", "document_id": "CSB-6275", "page": 13, "section": "Executive Summary",
        "query_text": "At a petroleum refinery, a pressurized process vessel released a large quantity of flammable naphtha. The liquid formed a vapor cloud that ignited as a flash fire, fatally injuring two workers and causing major facility damage. Find similar refinery incidents involving flammable-liquid releases, vapor-cloud formation, and ignition.",
        "facets": {"substance_or_hazard": ["naphtha", "flammable vapor cloud"], "equipment_or_process": ["refinery process vessel"], "failure_mechanism": ["loss of containment", "vapor-cloud ignition"], "event_sequence": ["liquid release", "vapor cloud", "flash fire"]},
    },
    {
        "query_id": "Q006", "document_id": "CSB-5965", "page": 9, "section": "Executive Summary",
        "query_text": "During gasoline transfer from a tanker ship to an onshore tank farm, a large aboveground storage tank overflowed into its secondary containment area. A gasoline spray formed a vapor cloud that ignited, leading to multiple explosions and a long-duration tank-farm fire with off-site damage. Find comparable incidents involving tank overfill, flammable vapor clouds, and terminal or refinery fires.",
        "facets": {"substance_or_hazard": ["gasoline", "flammable vapor cloud"], "equipment_or_process": ["ship unloading", "aboveground storage tank", "tank farm", "secondary containment"], "failure_mechanism": ["overfill", "vapor-cloud ignition"], "event_sequence": ["product transfer", "tank overflow", "vapor cloud", "multiple explosions/fire"]},
    },
    {
        "query_id": "Q007", "document_id": "CSB-5588", "page": 11, "section": "Executive Summary",
        "query_text": "A manufacturing facility producing acoustic insulation experienced a major dust explosion in its production area, followed by extensive fire and structural damage. Combustible resin dust was present in the facility, and workers were killed or injured. Find comparable combustible-dust incidents, especially where dust accumulation in production areas contributed to an explosion.",
        "facets": {"substance_or_hazard": ["combustible phenolic resin dust", "dust deflagration/explosion"], "equipment_or_process": ["manufacturing/production area", "acoustic insulation production"], "failure_mechanism": ["combustible dust accumulation and ignition"], "event_sequence": ["dust explosion", "fire/structural damage"]},
    },
    {
        "query_id": "Q008", "document_id": "CSB-6304", "page": 7, "section": "Executive Summary",
        "query_text": "At a food-processing facility using an industrial ammonia refrigeration system, an upset in the refrigeration process was followed by a discharge from an emergency pressure-relief valve. An ammonia cloud formed and exposed workers during evacuation, causing multiple injuries. Find comparable toxic-refrigerant releases and incidents involving pressure relief, refrigeration equipment, and occupied work areas.",
        "facets": {"substance_or_hazard": ["anhydrous ammonia", "toxic gas"], "equipment_or_process": ["industrial refrigeration", "heat exchanger/surge drum", "pressure-relief valve"], "failure_mechanism": ["overpressure", "emergency relief discharge"], "event_sequence": ["refrigeration upset", "relief-valve discharge", "toxic cloud", "worker exposure"]},
    },
    {
        "query_id": "Q009", "document_id": "CSB-6240", "page": 12, "section": "Executive Summary",
        "query_text": "A dry corn-milling facility experienced multiple combustible-dust explosions late in the evening during normal operations. Employees had noticed smoke in process areas before the explosions, which caused multiple fatalities, injuries, and extensive facility damage. Find comparable grain, food-processing, or other combustible-dust explosions and escalation sequences.",
        "facets": {"substance_or_hazard": ["combustible corn dust", "dust explosion"], "equipment_or_process": ["dry corn milling", "mills/process buildings"], "failure_mechanism": ["dust ignition and explosion propagation"], "event_sequence": ["smoke observed", "multiple explosions", "fire/damage"]},
    },
    {
        "query_id": "Q010", "document_id": "CSB-5660", "page": 8, "section": "Executive Summary",
        "query_text": "At a storage site, contractor personnel were conducting fireworks storage and disposal-related work inside a magazine used for seized pyrotechnics. An explosion and fire occurred while personnel were inside the magazine, resulting in multiple fatalities. Find comparable incidents involving energetic materials, fireworks handling or disassembly, and confined storage/work areas.",
        "facets": {"substance_or_hazard": ["fireworks", "energetic materials", "explosion/fire"], "equipment_or_process": ["storage magazine", "fireworks disposal"], "failure_mechanism": ["unintended initiation during handling/disassembly"], "event_sequence": ["disposal-related work", "magazine explosion", "fire"]},
    },
    {
        "query_id": "Q011", "document_id": "CSB-6316", "page": 8, "section": "Executive Summary",
        "query_text": "At a chemical plant's glycol production unit, a series of explosions and fires caused major process-equipment damage and a substantial release of toxic ethylene oxide. Local authorities issued a shelter-in-place order for nearby residents. Find relevant historical cases involving ethylene oxide, flammable/reactive process chemicals, pressure-relief systems, and explosion escalation.",
        "facets": {"substance_or_hazard": ["ethylene oxide", "toxic and flammable/reactive chemical"], "equipment_or_process": ["glycol plant", "process and pressure-relief piping"], "failure_mechanism": ["loss of containment", "ignition/explosion"], "event_sequence": ["process upset/release", "explosions", "fire", "off-site protective action"]},
    },
    {
        "query_id": "Q012", "document_id": "CSB-5602", "page": 11, "section": "Executive Summary",
        "query_text": "At a facility that unloads chlorine from railroad tank cars and repackages it into smaller containers, a large chlorine release continued for several hours. The toxic plume affected nearby residents and workers, leading to hospital evaluations, evacuation or shelter-in-place actions, and road closures. Find comparable chlorine transfer, hose failure, and toxic-gas release incidents.",
        "facets": {"substance_or_hazard": ["chlorine", "toxic gas"], "equipment_or_process": ["rail tank car unloading", "transfer hose", "repackaging"], "failure_mechanism": ["loss of containment during transfer"], "event_sequence": ["transfer operation", "prolonged release", "community exposure/protective action"]},
    },
    {
        "query_id": "Q013", "document_id": "CSB-6124", "page": 7, "section": "Executive Summary",
        "query_text": "At an insecticide manufacturing unit, a large quantity of highly toxic methyl mercaptan was released inside a production building, fatally exposing several operators and a shift supervisor. The event followed operating difficulties and troubleshooting during a restart sequence. Find comparable toxic-gas releases during abnormal operations, troubleshooting, or maintenance in chemical plants.",
        "facets": {"substance_or_hazard": ["methyl mercaptan", "toxic and flammable gas"], "equipment_or_process": ["insecticide production unit", "process piping", "building ventilation"], "failure_mechanism": ["toxic-gas loss of containment during abnormal operations"], "event_sequence": ["unit shutdown/restart troubleshooting", "gas release", "fatal exposure"]},
    },
    {
        "query_id": "Q014", "document_id": "CSB-6161", "page": 7, "section": "Executive Summary",
        "query_text": "During a planned shutdown at a paper mill, contractors were performing maintenance inside connected pulp-bleaching process vessels. A fire started inside one vessel while repair materials and temporary heating equipment were being used, causing worker injuries and a serious process-area fire. Find comparable hot-work, temporary-heating, flammable-resin, and confined-vessel fire incidents.",
        "facets": {"substance_or_hazard": ["flammable resin", "fire"], "equipment_or_process": ["pulp bleaching vessels", "maintenance inside process equipment"], "failure_mechanism": ["ignition during repair/maintenance"], "event_sequence": ["planned shutdown", "internal vessel repair", "fire", "worker injuries"]},
    },
    {
        "query_id": "Q015", "document_id": "CSB-6023", "page": 6, "section": "Executive Summary",
        "query_text": "At a refinery's fluid catalytic cracking unit, an electrostatic precipitator used for pollution control exploded while the unit was in an idled or standby condition and equipment isolation for maintenance was being prepared. Hydrocarbons entered the device and ignited, causing a major explosion and damage. Find comparable refinery incidents involving maintenance isolation, abnormal operating modes, hydrocarbon backflow, and ignition sources.",
        "facets": {"substance_or_hazard": ["hydrocarbons", "flammable atmosphere"], "equipment_or_process": ["FCC unit", "electrostatic precipitator", "maintenance isolation"], "failure_mechanism": ["backflow/loss of containment", "ignition in equipment"], "event_sequence": ["idled/standby mode", "maintenance preparation", "hydrocarbon ingress", "explosion"]},
    },
    {
        "query_id": "Q016", "document_id": "CSB-6268", "page": 8, "section": "Executive Summary",
        "query_text": "At a poultry-processing facility, liquid nitrogen overflowed from an immersion freezer inside a production building during troubleshooting work. The liquid rapidly vaporized and accumulated in a partially enclosed, poorly ventilated room, creating an oxygen-deficient atmosphere and causing multiple fatal asphyxiations and serious injuries. Find comparable cryogenic-gas releases, oxygen-deficiency hazards, and emergency-response incidents.",
        "facets": {"substance_or_hazard": ["liquid nitrogen", "asphyxiation/oxygen deficiency"], "equipment_or_process": ["immersion freezer", "partially enclosed room"], "failure_mechanism": ["cryogenic liquid release and vapor accumulation"], "event_sequence": ["troubleshooting", "overflow/release", "vaporization and accumulation", "fatal exposure/rescue attempts"]},
    },
    {
        "query_id": "Q017", "document_id": "CSB-6036", "page": 12, "section": "Executive Summary",
        "query_text": "At a chemical storage and distribution facility, a mixture leaked from an aboveground storage tank, escaped damaged secondary containment, and entered a nearby river used as a drinking-water source. The spill contaminated the water supply and prompted widespread public protective actions. Find comparable tank-integrity, secondary-containment, and chemical-spill-to-water incidents.",
        "facets": {"substance_or_hazard": ["industrial chemical mixture", "water contamination"], "equipment_or_process": ["aboveground storage tank", "secondary containment", "river"], "failure_mechanism": ["tank-floor corrosion/leak", "containment failure"], "event_sequence": ["tank leak", "escape from containment", "river entry", "drinking-water impact"]},
    },
    {
        "query_id": "Q018", "document_id": "CSB-6324", "page": 8, "section": "Executive Summary",
        "query_text": "At a food-ingredient manufacturing plant, a batch reactor vessel exploded while producing caramel coloring. The explosion killed and seriously injured workers and caused significant damage in the manufacturing area. Find comparable batch-reactor explosions, exothermic process upsets, and pressure-relief or vent-system incidents.",
        "facets": {"substance_or_hazard": ["caramel-coloring process", "exothermic decomposition/gas generation"], "equipment_or_process": ["batch reactor vessel", "reactor vent and pressure relief"], "failure_mechanism": ["reactor overpressure/explosion"], "event_sequence": ["batch manufacturing", "reactor explosion", "fire/worker casualties"]},
    },
    {
        "query_id": "Q019", "document_id": "CSB-6204", "page": 9, "section": "Executive Summary",
        "query_text": "During startup of a polymer-production reactor after a maintenance shutdown, high pressure activated an emergency relief system and discharged flammable ethylene vapor into an area where contractors were working. The vapor ignited, causing a fire and injuries to multiple workers. Find comparable reactor startup, pressure-relief discharge, vapor-cloud ignition, and worker-exposure incidents.",
        "facets": {"substance_or_hazard": ["ethylene", "flammable vapor"], "equipment_or_process": ["polymer reactor", "emergency pressure-relief system", "startup"], "failure_mechanism": ["overpressure and relief discharge", "external ignition"], "event_sequence": ["startup after turnaround", "high pressure", "relief discharge", "fire/worker injuries"]},
    },
    {
        "query_id": "Q020", "document_id": "CSB-6222", "page": 6, "section": "Executive Summary",
        "query_text": "During maintenance and isolation work in an acetic-acid production unit, contractors removed an actuator from a pressurized plug valve. A large quantity of corrosive chemical mixture was suddenly released from the open equipment, spraying workers and causing fatalities and serious injuries. Find comparable incidents involving valve design, inadvertent removal of pressure-retaining parts, and hazardous-energy isolation.",
        "facets": {"substance_or_hazard": ["acetic acid mixture", "methyl iodide", "corrosive/toxic liquid"], "equipment_or_process": ["plug valve", "actuator", "pipe-spool repair"], "failure_mechanism": ["pressurized equipment opening", "loss of pressure boundary"], "event_sequence": ["maintenance/isolation", "valve disassembly", "chemical release", "worker exposure"]},
    },
    {
        "query_id": "Q021", "document_id": "CSB-6286", "page": 7, "section": "Executive Summary",
        "query_text": "During initial startup of a renewable-diesel hydroprocessing unit, a tube inside a fired heater ruptured and released process hydrocarbons and hydrogen. The released material ignited, causing a fire and serious injury to a worker. Find comparable fired-heater, startup, tube-overheating, and hydrocarbon-release incidents.",
        "facets": {"substance_or_hazard": ["renewable diesel", "hydrogen", "flammable process stream"], "equipment_or_process": ["fired heater", "hydroprocessing unit", "startup"], "failure_mechanism": ["tube rupture/overheating", "loss of containment and ignition"], "event_sequence": ["initial startup", "heater tube failure", "release", "fire"]},
    },
    {
        "query_id": "Q022", "document_id": "CSB-6238", "page": 8, "section": "Executive Summary",
        "query_text": "At a chemical toll-manufacturing facility, a pressure-rated rotary double-cone dryer containing a chlorinated isocyanurate compound exploded. A subsequent fire and toxic chlorine release affected the site and nearby community. Find similar incidents involving reactive or chlorine-releasing chemicals, dryers, thermal decomposition, and vessel explosions.",
        "facets": {"substance_or_hazard": ["chlorinated isocyanurate", "chlorine release", "reactive chemical"], "equipment_or_process": ["pressure-rated rotary double-cone dryer"], "failure_mechanism": ["chemical decomposition/reaction", "equipment explosion"], "event_sequence": ["drying operation", "explosion", "fire", "toxic release"]},
    },
    {
        "query_id": "Q023", "document_id": "CSB-6144", "page": 8, "section": "Executive Summary",
        "query_text": "During a planned maintenance shutdown at a paperboard mill, a foul-condensate tank in a non-condensable-gas system exploded. The tank was propelled across the facility, killing and injuring contractors and heavily damaging nearby process equipment. Find comparable pulp-and-paper tank explosions, flammable vapor hazards, and maintenance-shutdown incidents.",
        "facets": {"substance_or_hazard": ["turpentine", "flammable vapor"], "equipment_or_process": ["foul-condensate tank", "non-condensable-gas system", "paperboard mill"], "failure_mechanism": ["flammable atmosphere ignition", "tank explosion"], "event_sequence": ["planned shutdown/maintenance", "tank explosion", "projectile and facility damage"]},
    },
    {
        "query_id": "Q024", "document_id": "CSB-6315", "page": 7, "section": "Executive Summary",
        "query_text": "During maintenance at a refinery, contractors opened piping containing pressurized hydrogen sulfide. A large toxic-gas release continued for nearly an hour, fatally injuring workers and exposing others in nearby units; community shelter-in-place actions followed. Find comparable H2S release, line-opening, isolation-verification, and contractor-exposure incidents.",
        "facets": {"substance_or_hazard": ["hydrogen sulfide", "toxic gas"], "equipment_or_process": ["refinery piping", "flange", "maintenance line opening"], "failure_mechanism": ["unexpected hazardous inventory in line", "loss of containment"], "event_sequence": ["maintenance", "line opening", "toxic gas release", "fatal exposure"]},
    },
    {
        "query_id": "Q025", "document_id": "CSB-5589", "page": 2, "section": "Abstract",
        "query_text": "At an oil-and-gas production facility, a catastrophic pressure-vessel failure was followed by a fire and multiple worker fatalities. The event occurred at a remote production site. Find historical incidents involving vessel overpressure, pressure-relief or design safeguards, and catastrophic failure in oil-and-gas operations.",
        "facets": {"substance_or_hazard": ["oil and gas process fluids", "flammable release/fire"], "equipment_or_process": ["pressure vessel", "production facility"], "failure_mechanism": ["catastrophic vessel failure/overpressure"], "event_sequence": ["vessel failure", "release/fire", "worker fatalities"]},
    },
    {
        "query_id": "Q026", "document_id": "CSB-6186", "page": 7, "section": "Executive Summary",
        "query_text": "At a crude-oil terminal during construction and piping modifications, a contractor was welding a piping segment. A flash fire and explosion occurred, ejecting isolation equipment and residual crude oil from the pipe and injuring several workers. Find comparable hot-work, line-isolation, residual-hydrocarbon, and contractor-safety incidents.",
        "facets": {"substance_or_hazard": ["crude oil", "flammable residual hydrocarbon"], "equipment_or_process": ["terminal piping", "isolation tools", "welding"], "failure_mechanism": ["ignition during hot work", "pressure buildup/ejection"], "event_sequence": ["piping modification", "welding", "flash fire/explosion", "worker injuries"]},
    },
    {
        "query_id": "Q027", "document_id": "CSB-6203", "page": 7, "section": "Executive Summary",
        "query_text": "At a petrochemical facility producing butadiene, a major process release was followed by a series of explosions and fires. Towers and other process equipment were damaged or displaced, fires continued for an extended period, and a large surrounding area was evacuated. Find similar butadiene or other flammable-vapor releases, vapor-cloud explosions, and major process-unit escalation events.",
        "facets": {"substance_or_hazard": ["butadiene", "highly flammable vapor"], "equipment_or_process": ["petrochemical process unit", "process tower"], "failure_mechanism": ["loss of containment", "vapor-cloud explosion"], "event_sequence": ["process release", "multiple explosions", "prolonged fire", "off-site evacuation"]},
    },
    {
        "query_id": "Q028", "document_id": "CSB-6296", "page": 8, "section": "Executive Summary",
        "query_text": "At a metal-treatment facility using molten-salt baths, workers were processing large rollers with internal cavities. An explosion-like eruption of molten salt occurred during the processing sequence, causing a fatal injury and fires throughout the facility. Find comparable incidents involving molten materials, trapped liquids, hot processes, and unexpected pressure or violent ejection.",
        "facets": {"substance_or_hazard": ["molten salts", "hot liquid", "thermal hazard"], "equipment_or_process": ["liquid nitriding", "rollers with internal cavities", "hot-water rinse bath"], "failure_mechanism": ["retained liquid/pressure buildup", "violent ejection"], "event_sequence": ["immersion processing", "lifting/rinsing", "molten-salt eruption", "fire"]},
    },
    {
        "query_id": "Q029", "document_id": "CSB-6340", "page": 7, "section": "Executive Summary",
        "query_text": "During maintenance on an isolation valve in coke-oven-gas piping at an integrated steelworks, employees and contractors were manipulating the valve and using pressurized water to clean its seats. A toxic and flammable gas release and explosion caused multiple fatalities, injuries, and significant property damage. Find comparable incidents involving gas-system isolation, valve maintenance, water washing, and hazardous-energy control.",
        "facets": {"substance_or_hazard": ["coke-oven gas", "toxic and flammable gas mixture"], "equipment_or_process": ["double-disc gate isolation valve", "gas-supply piping"], "failure_mechanism": ["hazardous gas release during valve maintenance", "ignition/explosion"], "event_sequence": ["valve maintenance/washing", "gas release", "explosion", "worker casualties"]},
    },
    {
        "query_id": "Q030", "document_id": "CSB-6267", "page": 9, "section": "Executive Summary",
        "query_text": "During a workover operation at an oil-and-gas well, the well lost control and hydrocarbons escaped to the surface. The released oil and gas ignited within seconds, causing a flash fire that fatally injured several contractors and seriously injured another. Find comparable well-control, blowout, ignition, and contractor-safety incidents.",
        "facets": {"substance_or_hazard": ["oil and gas hydrocarbons", "flammable release/fire"], "equipment_or_process": ["wellhead", "well workover operation"], "failure_mechanism": ["loss of well control/blowout", "ignition"], "event_sequence": ["workover", "uncontrolled release", "rapid ignition", "flash fire"]},
    },
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    temp.replace(path)


def short_quote(text: str, max_words: int = 24) -> str:
    words = re.sub(r"\s+", " ", text).strip().split()
    return " ".join(words[:max_words])


def build_queries() -> list[dict[str, Any]]:
    incidents = read_jsonl(DATA / "incident_catalog.jsonl")
    documents = read_jsonl(DATA / "chunk_store/documents.jsonl")
    pages = read_jsonl(DATA / "chunk_store/pages.jsonl")
    incident_by_doc: dict[str, list[dict[str, Any]]] = {}
    for incident in incidents:
        for document_id in incident.get("primary_document_ids", []):
            incident_by_doc.setdefault(document_id, []).append(incident)
    doc_by_id = {row["document_id"]: row for row in documents}
    page_by_key = {(row["document_id"], row["page"]): row for row in pages}

    output: list[dict[str, Any]] = []
    for seed in SEEDS:
        document_id = seed["document_id"]
        if document_id not in doc_by_id:
            raise ValueError(f"Seed {seed['query_id']} source document missing from chunk_store: {document_id}")
        candidates = incident_by_doc.get(document_id, [])
        if len(candidates) != 1:
            raise ValueError(f"Seed {seed['query_id']} source document maps to {len(candidates)} incidents, expected exactly one")
        incident = candidates[0]
        if not incident.get("source_case_exclusion_ready"):
            raise ValueError(f"Seed {seed['query_id']} source incident is not ready for source-case exclusion")
        if len(incident.get("investigation_ids", [])) != 1:
            raise ValueError(f"Seed {seed['query_id']} should represent exactly one source investigation")
        page = page_by_key.get((document_id, seed["page"]))
        if not page:
            raise ValueError(f"Seed {seed['query_id']} source page not found: {document_id} p.{seed['page']}")
        if page.get("page_kind") == "table_of_contents" or len(page.get("text", "")) < 250:
            raise ValueError(f"Seed {seed['query_id']} source page is not a substantive report page")
        if document_id not in incident.get("source_case_exclusion", {}).get("document_ids", []):
            raise ValueError(f"Source document {document_id} is absent from its exclusion bundle")

        exclusion = incident["source_case_exclusion"]
        source_doc = doc_by_id[document_id]
        record = {
            "query_id": seed["query_id"],
            "query_text": seed["query_text"],
            "query_status": "draft_for_human_review",
            "language": "en",
            "query_facets": seed["facets"],
            "source_case": {
                "source_incident_id": incident["incident_id"],
                "source_investigation_id": incident["investigation_ids"][0],
                "source_case_id": (exclusion.get("source_case_ids") or [None])[0],
                "source_document_id": document_id,
                "source_document_title": source_doc.get("title"),
                "source_mapping_status": incident.get("mapping_status"),
                "source_catalog_status": incident.get("catalog_status"),
                "source_parse_quality_status": source_doc.get("parse_quality", {}).get("quality_status"),
                "source_evidence": {
                    "document_id": document_id,
                    "page": seed["page"],
                    "section": seed["section"],
                    "supporting_quote": short_quote(page.get("text", "")),
                },
            },
            "source_case_exclusion": {
                "scope": exclusion.get("scope"),
                "incident_id": exclusion.get("incident_id"),
                "source_case_ids": exclusion.get("source_case_ids", []),
                "investigation_ids": exclusion.get("investigation_ids", []),
                "document_ids": exclusion.get("document_ids", []),
                "recommendation_ids": exclusion.get("recommendation_ids", []),
                "recommendation_snapshot_date": exclusion.get("recommendation_snapshot_date"),
                "status": incident.get("source_case_exclusion_status"),
            },
            "expected_abstention": None,
            "relevance_annotation_status": "pending_retrieval_pool_and_human_labels",
            "reference_answer_status": "pending_relevance_labels",
        }
        output.append(record)

    if len({row["query_id"] for row in output}) != len(output):
        raise ValueError("Duplicate query_id in seed definitions")
    if len({row["source_case"]["source_investigation_id"] for row in output}) != len(output):
        raise ValueError("Source investigations must be unique across the initial query seeds")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=GOLDEN)
    args = parser.parse_args()
    records = build_queries()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.output_dir / "query_seeds.jsonl", records)

    review_csv = args.output_dir / "query_review.csv"
    fields = [
        "query_id", "query_text", "source_document_id", "source_document_title",
        "source_page", "source_section", "source_incident_id", "source_investigation_id",
        "source_mapping_status", "excluded_document_ids", "excluded_recommendation_ids",
        "query_status",
    ]
    with review_csv.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            src = row["source_case"]
            ev = src["source_evidence"]
            writer.writerow({
                "query_id": row["query_id"], "query_text": row["query_text"],
                "source_document_id": src["source_document_id"],
                "source_document_title": src["source_document_title"],
                "source_page": ev["page"], "source_section": ev["section"],
                "source_incident_id": src["source_incident_id"],
                "source_investigation_id": src["source_investigation_id"],
                "source_mapping_status": src["source_mapping_status"],
                "excluded_document_ids": ";".join(row["source_case_exclusion"]["document_ids"]),
                "excluded_recommendation_ids": ";".join(row["source_case_exclusion"]["recommendation_ids"]),
                "query_status": row["query_status"],
            })

    summary = {
        "query_seed_count": len(records),
        "unique_source_investigations": len({r["source_case"]["source_investigation_id"] for r in records}),
        "source_mapping_status_counts": {},
        "source_exclusion_ready_count": sum(r["source_case_exclusion"]["scope"] == "whole_investigation" for r in records),
        "query_texts_anonymized": True,
        "relevance_labels_created": 0,
        "reference_answers_created": 0,
        "status": "draft_seeds_only_not_yet_a_final_golden_set",
    }
    from collections import Counter
    summary["source_mapping_status_counts"] = dict(Counter(r["source_case"]["source_mapping_status"] for r in records))
    (args.output_dir / "seed_build_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
