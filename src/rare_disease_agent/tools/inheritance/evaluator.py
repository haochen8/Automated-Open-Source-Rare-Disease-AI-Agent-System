"""Deterministic inheritance-model evaluators."""

from __future__ import annotations

from collections import defaultdict

from rare_disease_agent.reporting.provenance import SoftwareIdentity, evidence_provenance
from rare_disease_agent.tools.inheritance.schemas import (
    CompoundHeterozygousPair,
    GenotypeCall,
    InheritanceEvaluation,
    InheritanceEvidence,
    InheritanceModel,
    InheritanceSummary,
    Pedigree,
    VariantGenotypes,
)

TOOL_VERSION = "inheritance-evaluator-v1"


class InheritanceEvaluator:
    def __init__(
        self,
        pedigree: Pedigree,
        *,
        run_id: str,
        minimum_genotype_quality: float = 20,
        software: SoftwareIdentity | None = None,
    ) -> None:
        self.pedigree = pedigree
        self.run_id = run_id
        self.minimum_genotype_quality = minimum_genotype_quality
        self.software = software

    def _provenance(self, method: str) -> object:
        return evidence_provenance(
            run_id=self.run_id,
            tool_version=TOOL_VERSION,
            data_version="pedigree-input-v1",
            method=method,
            parameters={"minimum_genotype_quality": self.minimum_genotype_quality},
            software=self.software,
        )

    def _calls(
        self, variant: VariantGenotypes
    ) -> tuple[GenotypeCall | None, GenotypeCall | None, GenotypeCall | None]:
        return (
            variant.call(self.pedigree.proband_id),
            variant.call(self.pedigree.mother_id),
            variant.call(self.pedigree.father_id),
        )

    def _quality(self, calls: list[GenotypeCall | None]) -> tuple[bool, list[str]]:
        observed = [call for call in calls if call and call.called]
        low = [
            call.individual_id
            for call in observed
            if call.quality is not None and call.quality < self.minimum_genotype_quality
        ]
        low_fraction = any(
            call.alternate_fraction is not None
            and call.has_alternate
            and call.alternate_fraction < 0.25
            for call in observed
        )
        if low_fraction:
            return False, [
                "Low alternate allele fraction; mosaicism or technical artifact remains uncertain."
            ]
        if low:
            return False, ["Low genotype quality for: " + ", ".join(sorted(low)) + "."]
        return True, []

    def _result(
        self,
        variant: VariantGenotypes,
        *,
        model: InheritanceModel,
        fit: float,
        evidence: list[str],
        warnings: list[str],
        required_calls: list[GenotypeCall | None],
    ) -> InheritanceEvidence:
        proband, mother, father = self._calls(variant)
        quality_passed, quality_warnings = self._quality(required_calls)
        if not quality_passed:
            fit = min(fit, 0.5)
        return InheritanceEvidence(
            variant_id=variant.variant_id,
            gene=variant.gene,
            model=model,
            fit=round(fit, 4),
            proband_genotype=proband.genotype if proband else None,
            mother_genotype=mother.genotype if mother else None,
            father_genotype=father.genotype if father else None,
            quality_checks_passed=quality_passed,
            evidence=evidence,
            warnings=[*warnings, *quality_warnings],
            provenance=self._provenance(model),
        )

    def evaluate_de_novo(self, variant: VariantGenotypes) -> InheritanceEvidence:
        proband, mother, father = self._calls(variant)
        evidence: list[str] = []
        warnings: list[str] = []
        if not proband or not proband.called or not proband.has_alternate:
            fit = 0.0
            warnings.append("The proband does not have a called alternate genotype.")
        elif not mother or not father or not mother.called or not father.called:
            fit = 0.4
            warnings.append("Parental genotype evidence is incomplete; de novo is unconfirmed.")
        elif mother.is_reference and father.is_reference:
            fit = 1.0
            evidence.append("Proband alternate with both parents called reference.")
        else:
            fit = 0.0
            evidence.append("An alternate allele is observed in a parent.")
        return self._result(
            variant,
            model="de_novo",
            fit=fit,
            evidence=evidence,
            warnings=warnings,
            required_calls=[proband, mother, father],
        )

    def evaluate_dominant(self, variant: VariantGenotypes) -> InheritanceEvidence:
        proband, _, _ = self._calls(variant)
        evidence: list[str] = []
        warnings: list[str] = []
        if not proband or not proband.called or not proband.has_alternate:
            fit = 0.0
            warnings.append("The affected proband has no called alternate allele.")
        else:
            informative = 0
            consistent = 0
            for individual in self.pedigree.individuals:
                if individual.affected is None:
                    continue
                call = variant.call(individual.id)
                if not call or not call.called:
                    continue
                informative += 1
                if (individual.affected and call.has_alternate) or (
                    not individual.affected and call.is_reference
                ):
                    consistent += 1
            fit = consistent / informative if informative else 0.5
            evidence.append(
                f"{consistent} of {informative} phenotype-informative calls fit dominance."
            )
            if consistent < informative:
                warnings.append(
                    "Segregation is incomplete; incomplete penetrance or a competing cause "
                    "remains possible."
                )
            if informative < 2:
                warnings.append("Dominant segregation evidence is sparse.")
        return self._result(
            variant,
            model="autosomal_dominant",
            fit=fit,
            evidence=evidence,
            warnings=warnings,
            required_calls=[variant.call(item.id) for item in self.pedigree.individuals],
        )

    def evaluate_homozygous_recessive(self, variant: VariantGenotypes) -> InheritanceEvidence:
        proband, mother, father = self._calls(variant)
        evidence: list[str] = []
        warnings: list[str] = []
        if not proband or not proband.called or not proband.is_homozygous_alternate:
            fit = 0.0
            warnings.append("The proband is not called homozygous alternate.")
        elif not mother or not father or not mother.called or not father.called:
            fit = 0.65
            warnings.append("Parental carrier evidence is incomplete.")
        elif mother.is_heterozygous and father.is_heterozygous:
            fit = 1.0
            evidence.append(
                "Homozygous proband and two heterozygous parents support recessive inheritance."
            )
        else:
            fit = 0.35
            warnings.append("Parental genotypes do not show the expected two-carrier pattern.")
        return self._result(
            variant,
            model="homozygous_recessive",
            fit=fit,
            evidence=evidence,
            warnings=warnings,
            required_calls=[proband, mother, father],
        )

    def evaluate_x_linked(self, variant: VariantGenotypes) -> InheritanceEvidence:
        proband, mother, father = self._calls(variant)
        proband_person = self.pedigree.individual(self.pedigree.proband_id)
        evidence: list[str] = []
        warnings: list[str] = []
        if variant.chromosome.upper().removeprefix("CHR") != "X":
            fit = 0.0
            warnings.append("Variant is not on chromosome X.")
        elif not proband_person or proband_person.sex != "male":
            fit = 0.2
            warnings.append("This baseline currently models affected male X-linked cases.")
        elif not proband or not proband.called or not proband.has_alternate:
            fit = 0.0
            warnings.append("Affected male proband lacks a called alternate allele.")
        elif mother and mother.called and mother.is_heterozygous:
            fit = 1.0 if father is None or father.is_reference else 0.9
            evidence.append(
                "Affected male proband and heterozygous mother support X-linked inheritance."
            )
        else:
            fit = 0.55
            warnings.append("Maternal carrier evidence is absent or incomplete.")
        return self._result(
            variant,
            model="x_linked",
            fit=fit,
            evidence=evidence,
            warnings=warnings,
            required_calls=[proband, mother, father],
        )

    def find_compound_heterozygous_pairs(
        self, variants: list[VariantGenotypes]
    ) -> list[CompoundHeterozygousPair]:
        by_gene: dict[str, list[VariantGenotypes]] = defaultdict(list)
        for variant in variants:
            proband = variant.call(self.pedigree.proband_id)
            if proband and proband.is_heterozygous:
                by_gene[variant.gene.upper()].append(variant)
        pairs: list[CompoundHeterozygousPair] = []
        for gene, candidates in sorted(by_gene.items()):
            for index, first in enumerate(candidates):
                for second in candidates[index + 1 :]:
                    first_origin = self._parental_origin(first)
                    second_origin = self._parental_origin(second)
                    evidence: list[str] = []
                    warnings: list[str] = []
                    if {first_origin, second_origin} == {"maternal", "paternal"}:
                        pair_calls = [
                            first.call(self.pedigree.proband_id),
                            first.call(self.pedigree.mother_id),
                            first.call(self.pedigree.father_id),
                            second.call(self.pedigree.proband_id),
                            second.call(self.pedigree.mother_id),
                            second.call(self.pedigree.father_id),
                        ]
                        quality_passed, _ = self._quality(pair_calls)
                        if quality_passed:
                            phase = "confirmed_trans"
                            confidence = 0.95
                            evidence.append("Opposite parental origins confirm trans inheritance.")
                        else:
                            phase = "possible_trans"
                            confidence = 0.5
                            warnings.append(
                                "Opposite parental origins were observed, but low genotype quality "
                                "prevents confirmed-trans classification."
                            )
                    elif first_origin == second_origin and first_origin in {"maternal", "paternal"}:
                        phase = "unknown"
                        confidence = 0.25
                        warnings.append(
                            "Same parental origin may indicate cis; trans is unconfirmed."
                        )
                    elif first_origin in {"maternal", "paternal"} or second_origin in {
                        "maternal",
                        "paternal",
                    }:
                        phase = "possible_trans"
                        confidence = 0.65
                        warnings.append(
                            "Only one parental origin is informative; trans is possible."
                        )
                    else:
                        first_call = first.call(self.pedigree.proband_id)
                        second_call = second.call(self.pedigree.proband_id)
                        phased_opposite = bool(
                            first_call
                            and second_call
                            and "|" in (first_call.genotype or "")
                            and "|" in (second_call.genotype or "")
                            and first_call.genotype != second_call.genotype
                        )
                        phase = "possible_trans" if phased_opposite else "unknown"
                        confidence = 0.7 if phased_opposite else 0.55
                        warnings.append(
                            "Parental phase is not confirmed; the pair must remain uncertain."
                        )
                    pairs.append(
                        CompoundHeterozygousPair(
                            gene=gene,
                            variant_a=first.variant_id,
                            variant_b=second.variant_id,
                            phase=phase,
                            confidence=confidence,
                            evidence=evidence,
                            warnings=warnings,
                            provenance=self._provenance("compound_heterozygous"),
                        )
                    )
        return pairs

    def _parental_origin(self, variant: VariantGenotypes) -> str:
        _, mother, father = self._calls(variant)
        if not mother or not father or not mother.called or not father.called:
            return "unknown"
        if mother.has_alternate and father.is_reference:
            return "maternal"
        if father.has_alternate and mother.is_reference:
            return "paternal"
        return "unknown"

    def evaluate(self, variants: list[VariantGenotypes]) -> InheritanceEvaluation:
        evidence: list[InheritanceEvidence] = []
        for variant in variants:
            evidence.extend(
                [
                    self.evaluate_de_novo(variant),
                    self.evaluate_dominant(variant),
                    self.evaluate_homozygous_recessive(variant),
                    self.evaluate_x_linked(variant),
                ]
            )
        pairs = self.find_compound_heterozygous_pairs(variants)
        for pair in pairs:
            for variant_id in (pair.variant_a, pair.variant_b):
                variant = next(item for item in variants if item.variant_id == variant_id)
                proband, mother, father = self._calls(variant)
                quality_passed, _ = self._quality([proband, mother, father])
                evidence.append(
                    InheritanceEvidence(
                        variant_id=variant_id,
                        gene=pair.gene,
                        model="compound_heterozygous",
                        fit=pair.confidence if quality_passed else min(pair.confidence, 0.5),
                        proband_genotype=proband.genotype if proband else None,
                        mother_genotype=mother.genotype if mother else None,
                        father_genotype=father.genotype if father else None,
                        quality_checks_passed=quality_passed,
                        evidence=pair.evidence,
                        warnings=pair.warnings,
                        provenance=pair.provenance,
                    )
                )
        evidence.extend(self._autosomal_recessive_evidence(variants, evidence))
        summaries = []
        models: list[InheritanceModel] = [
            "de_novo",
            "autosomal_dominant",
            "homozygous_recessive",
            "compound_heterozygous",
            "autosomal_recessive",
            "x_linked",
        ]
        for model in models:
            matching = [item for item in evidence if item.model == model]
            ranked = sorted(matching, key=lambda item: (-item.fit, item.variant_id))
            summaries.append(
                InheritanceSummary(
                    model=model,
                    strong_matches=sum(item.fit >= 0.8 for item in matching),
                    uncertain_matches=sum(0.4 <= item.fit < 0.8 for item in matching),
                    top_variant_ids=[item.variant_id for item in ranked[:5] if item.fit > 0],
                )
            )
        return InheritanceEvaluation(
            evidence=evidence,
            compound_heterozygous_pairs=pairs,
            summaries=summaries,
        )

    def _autosomal_recessive_evidence(
        self,
        variants: list[VariantGenotypes],
        evidence: list[InheritanceEvidence],
    ) -> list[InheritanceEvidence]:
        result: list[InheritanceEvidence] = []
        for variant in variants:
            candidates = [
                item
                for item in evidence
                if item.variant_id == variant.variant_id
                and item.model in {"homozygous_recessive", "compound_heterozygous"}
            ]
            best = max(candidates, key=lambda item: item.fit, default=None)
            proband, mother, father = self._calls(variant)
            result.append(
                self._result(
                    variant,
                    model="autosomal_recessive",
                    fit=best.fit if best else 0.0,
                    evidence=["Best homozygous or compound-heterozygous recessive evidence."],
                    warnings=[] if best else ["No recessive genotype pattern identified."],
                    required_calls=[proband, mother, father],
                )
            )
        return result
