"""将已缓存的 iPlant 原文转换为字段级候选；本模块不读写 Excel。"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
import re

from qinchecker.models.review import (
    Confidence,
    FieldKey,
    FieldProposal,
    ReviewState,
    SpeciesRecord,
)
from qinchecker.models.source import SourceSection, SourceSnapshot


MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

PLACE_NAMES = {
    "Anhui": "安徽", "Beijing": "北京", "Chongqing": "重庆", "Fujian": "福建",
    "Gansu": "甘肃", "Guangdong": "广东", "Guangxi": "广西", "Guizhou": "贵州",
    "Hebei": "河北", "Heilongjiang": "黑龙江", "Henan": "河南", "Hubei": "湖北",
    "Hunan": "湖南", "Inner Mongolia": "内蒙古", "Jiangsu": "江苏", "Jiangxi": "江西",
    "Jilin": "吉林", "Liaoning": "辽宁", "Ningxia": "宁夏", "Qinghai": "青海",
    "Shaanxi": "陕西", "Shandong": "山东", "Shanghai": "上海", "Shanxi": "山西",
    "Sichuan": "四川", "Tianjin": "天津", "Tibet": "西藏", "Xinjiang": "新疆",
    "Yunnan": "云南", "Zhejiang": "浙江", "Taiwan": "台湾", "Hong Kong": "香港",
    "Macau": "澳门", "Japan": "日本", "Korea": "朝鲜半岛", "Vietnam": "越南",
    "Laos": "老挝", "Myanmar": "缅甸", "Thailand": "泰国", "India": "印度",
    "Nepal": "尼泊尔", "Bhutan": "不丹", "Pakistan": "巴基斯坦", "Russia": "俄罗斯",
    "Mongolia": "蒙古", "Philippines": "菲律宾", "Malaysia": "马来西亚",
    "Indonesia": "印度尼西亚", "Afghanistan": "阿富汗", "Kazakhstan": "哈萨克斯坦",
}

CHINA_LOCATION_TERMS = {
    "China", "Anhui", "Beijing", "Chongqing", "Fujian", "Gansu", "Guangdong", "Guangxi", "Guizhou",
    "Hebei", "Heilongjiang", "Henan", "Hubei", "Hunan", "Inner Mongolia", "Jiangsu", "Jiangxi",
    "Jilin", "Liaoning", "Ningxia", "Qinghai", "Shaanxi", "Shandong", "Shanghai", "Shanxi",
    "Sichuan", "Tianjin", "Tibet", "Xinjiang", "Yunnan", "Zhejiang", "Taiwan", "Hong Kong", "Macau",
}


@dataclass(slots=True)
class ParseResult:
    proposals: list[FieldProposal] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CountyInfo:
    chinese_name: str
    english_name: str
    slope: str


class HabitatGlossary:
    # FOC 英文生境与既有中文描述的保守语义对照。命中只允许“保留原值”，
    # 不会据此生成新的中文翻译。
    SIMILARITY_CUES = {
        "forest": ("林", "林下", "疏林", "阔叶"),
        "thicket": ("灌", "丛"),
        "slope": ("山坡", "坡", "山地"),
        "mountain": ("山", "坡", "岭"),
        "valley": ("山谷", "谷", "沟"),
        "meadow": ("草地", "草甸", "草坡"),
        "grassland": ("草地", "草甸", "草坡"),
        "river": ("河", "溪", "岸", "水边", "湿地"),
        "stream": ("河", "溪", "沟", "水边"),
        "riverbank": ("河", "溪", "岸", "水边"),
        "rock": ("石", "岩"),
        "cliff": ("石", "岩", "崖"),
        "marsh": ("湿地", "沼泽"),
        "swamp": ("湿地", "沼泽"),
        "field": ("田", "农地"),
    }
    def __init__(self, translations: dict[str, str] | None = None) -> None:
        self.translations = translations or {}

    @classmethod
    def from_csv(cls, path: Path) -> "HabitatGlossary":
        translations: dict[str, str] = {}
        with path.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                english = (row.get("english_term") or "").strip()
                chinese = (row.get("chinese_term") or "").strip()
                if english and chinese:
                    translations[cls.normalize(english)] = chinese
        return cls(translations)

    @staticmethod
    def normalize(value: str) -> str:
        return " ".join(value.casefold().strip(" .;,").split())

    def translate_exact(self, english: str) -> str | None:
        return self.translations.get(self.normalize(english))

    def compatible_with_original(self, english: str, original_chinese: object) -> bool:
        chinese = str(original_chinese or "").strip()
        if not chinese or chinese == "-":
            return False
        lowered = self.normalize(english)
        for english_cue, chinese_cues in self.SIMILARITY_CUES.items():
            if re.search(rf"\b{re.escape(english_cue)}(?:s|es)?\b", lowered) and any(cue in chinese for cue in chinese_cues):
                return True
        return False


class CountyIndex:
    """区县词典。

    ``verified`` 条目才会作为自动合并的白名单；``candidate`` 只用于提示
    复核，``excluded`` 则明确不纳入。这样不完整的秦岭范围清单不会被误当作
    已核验事实。
    """

    def __init__(
        self,
        aliases: dict[str, str] | None = None,
        candidates: dict[str, str] | None = None,
        excluded: dict[str, str] | None = None,
        target_provinces: set[str] | None = None,
        metadata: dict[str, CountyInfo] | None = None,
    ) -> None:
        self.aliases = aliases or {}
        self.candidates = candidates or {}
        self.excluded = excluded or {}
        self.target_provinces = target_provinces or set()
        self.metadata = metadata or {}

    @classmethod
    def from_csv(cls, county_path: Path, alias_path: Path) -> "CountyIndex":
        verified: dict[str, str] = {}
        candidates: dict[str, str] = {}
        excluded: dict[str, str] = {}
        target_provinces: set[str] = set()
        metadata: dict[str, CountyInfo] = {}

        def add(mapping: dict[str, str], standard: str, raw_aliases: str) -> None:
            mapping[standard] = standard
            for alias in raw_aliases.split("|"):
                if alias.strip():
                    mapping[alias.strip()] = standard

        if county_path.exists():
            with county_path.open(encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file):
                    standard = (row.get("standard_name") or "").strip()
                    if not standard:
                        continue
                    status = (row.get("status") or "candidate").strip().casefold()
                    raw_aliases = row.get("aliases") or ""
                    province = cls._normalize_province(row.get("province") or "")
                    english_name = (row.get("english_name") or "").strip()
                    slope = (row.get("slope") or "").strip()
                    if english_name and slope:
                        metadata[standard] = CountyInfo(standard, english_name, slope)
                    if status != "excluded" and province:
                        target_provinces.add(province)
                    if status == "verified":
                        add(verified, standard, raw_aliases)
                    elif status == "excluded":
                        add(excluded, standard, raw_aliases)
                    else:
                        add(candidates, standard, raw_aliases)

        # 行政区别名只在标准名已处于相同状态时生效，不能单靠一条别名就启用自动写入。
        if alias_path.exists():
            with alias_path.open(encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file):
                    standard = (row.get("standard_name") or "").strip()
                    alias = (row.get("alias_name") or row.get("aliases") or "").strip()
                    if not standard or not alias:
                        continue
                    for mapping in (verified, candidates, excluded):
                        if standard in mapping:
                            mapping[alias] = standard
                            break
        return cls(verified, candidates, excluded, target_provinces, metadata)

    def normalize(self, value: str) -> str | None:
        return self.aliases.get(value.strip())

    @staticmethod
    def _normalize_province(value: str) -> str:
        return re.sub(r"(?:省|市|自治区|特别行政区)$", "", value.strip())

    def extract(self, distribution_text: str) -> tuple[list[str], list[str]]:
        found: list[str] = []
        unknown: list[str] = []
        for line in distribution_text.splitlines():
            match = re.match(r"^\s*([^：:]+)[：:]\s*(.+)$", line)
            if not match:
                continue
            if self.target_provinces and self._normalize_province(match.group(1)) not in self.target_provinces:
                continue
            raw_names = match.group(2)
            for raw_name in re.split(r"[、，,；;]", raw_names):
                name = raw_name.strip()
                if not name:
                    continue
                standard = self.normalize(name)
                if standard and standard not in found:
                    found.append(standard)
                elif name not in unknown:
                    unknown.append(name)
        return found, unknown

    def describe(self, counties: list[str]) -> list[CountyInfo]:
        return [self.metadata[county] for county in counties if county in self.metadata]

    def has_external_province(self, distribution_text: str) -> bool:
        for line in distribution_text.splitlines():
            match = re.match(r"^\s*([^：:]+)[：:]\s*(.+)$", line)
            if match and self.target_provinces:
                if self._normalize_province(match.group(1)) not in self.target_provinces:
                    return True
        return False


class SourceParser:
    def __init__(self, habitat_glossary: HabitatGlossary, county_index: CountyIndex) -> None:
        self.habitat_glossary = habitat_glossary
        self.county_index = county_index

    def parse(self, record: SpeciesRecord, snapshot: SourceSnapshot) -> ParseResult:
        result = ParseResult()
        if snapshot.foc.usable:
            foc_taxonomy = self._parse_foc_taxonomy(record, snapshot.foc)
            result.proposals.extend(foc_taxonomy)
            result.proposals.extend(self._parse_foc(record, snapshot.foc))
            # FOC 页无法稳定取得名称时，才以 iPlant 名称页作中等置信度的待复核补充。
            if not foc_taxonomy and snapshot.nomenclature.usable:
                result.proposals.extend(self._parse_nomenclature(record, snapshot.nomenclature))
                result.notices.append("FOC 名称标题未能解析；名称分类页仅作为待复核补充")
        else:
            result.notices.append("FOC 来源不可用")
            if snapshot.nomenclature.usable:
                result.proposals.extend(self._parse_nomenclature(record, snapshot.nomenclature))
                result.notices.append("使用名称信息作为人工补充")
        if snapshot.county_distribution.usable:
            county_proposal, counties, unknown = self._parse_counties(record, snapshot.county_distribution)
            if county_proposal is not None:
                result.proposals.append(county_proposal)
            result.proposals.extend(self._parse_qinling_fields(record, snapshot.county_distribution, counties, unknown))
            if unknown:
                result.notices.append(f"县级分布含 {len(unknown)} 个未列入秦岭白名单的地名")
        else:
            result.notices.append("县级分布来源不可用")
        self._ensure_all_fields(record, snapshot, result)
        if snapshot.used_chinese_fallback:
            search_note = f"拉丁名未搜索到有效 FOC，已改用中文名“{snapshot.searched_name or record.chinese_name}”搜索"
            for proposal in result.proposals:
                proposal.note = f"{search_note}；{proposal.note}" if proposal.note else search_note
        return result

    def _parse_foc_taxonomy(self, record: SpeciesRecord, source: SourceSection) -> list[FieldProposal]:
        text = source.text
        proposals: list[FieldProposal] = []
        taxon_match = re.search(r"(?m)^\s*\d+\.\s*([A-Z][^\n]+)$", text)
        if taxon_match:
            taxon_line = taxon_match.group(1).strip()
            accepted = self._strip_author(taxon_line)
            if accepted:
                proposals.append(self._proposal(record, FieldKey.SPECIES_LATIN, accepted, Confidence.HIGH, source, taxon_line, "FOC 物种标题"))
            name_line = re.search(rf"(?m)^\s*\d+\.\s*[^\n]+\n\s*([\u3400-\u9fff]+)(?:\s+[a-z][a-z -]*)?\s*$", text)
            if name_line:
                proposals.append(self._proposal(record, FieldKey.SPECIES_CHINESE, name_line.group(1), Confidence.HIGH, source, name_line.group(0), "FOC 物种标题"))
        family_match = re.search(r"(?m)^FOC\s*>>.*?>>\s*([A-Z][A-Za-z-]+aceae)\s*>>", text)
        if family_match:
            family = family_match.group(1).strip()
            proposals.append(self._proposal(record, FieldKey.FAMILY_LATIN, family, Confidence.HIGH, source, family_match.group(0), "FOC 分类路径"))
        return proposals

    def _parse_nomenclature(self, record: SpeciesRecord, source: SourceSection) -> list[FieldProposal]:
        text = source.text
        proposals: list[FieldProposal] = []
        latin = self._label_value(text, "学名")
        chinese = self._label_value(text, "中文名")
        if latin:
            accepted = self._strip_author(latin)
            if accepted:
                proposals.append(self._proposal(record, FieldKey.SPECIES_LATIN, accepted, Confidence.MEDIUM, source, latin, "FOC 标题缺失时的名称页补充"))
        if chinese:
            proposals.append(self._proposal(record, FieldKey.SPECIES_CHINESE, chinese, Confidence.MEDIUM, source, chinese, "FOC 标题缺失时的名称页补充"))
        family_match = re.search(r"^科\s+([A-Za-z][A-Za-z -]+)-([^\n(（]+)", text, re.MULTILINE)
        if family_match:
            latin_family = family_match.group(1).strip()
            chinese_family = family_match.group(2).strip()
            excerpt = family_match.group(0)
            proposals.append(self._proposal(record, FieldKey.FAMILY_LATIN, latin_family, Confidence.MEDIUM, source, excerpt, "FOC 标题缺失时的名称页补充"))
            proposals.append(self._proposal(record, FieldKey.FAMILY_CHINESE, chinese_family, Confidence.MEDIUM, source, excerpt, "FOC 标题缺失时的名称页补充"))
        return proposals

    def _parse_foc(self, record: SpeciesRecord, source: SourceSection) -> list[FieldProposal]:
        proposals: list[FieldProposal] = []
        proposals.append(
            self._proposal(
                record,
                FieldKey.SOURCE,
                "iPlant — Flora of China (FOC)",
                Confidence.HIGH,
                source,
                source.resolved_url or source.requested_url,
                "FOC 来源页面",
            )
        )
        elevation = self._extract_elevation(source.text)
        if elevation is not None:
            low, high, excerpt, note = elevation
            proposals.append(self._proposal(record, FieldKey.LOWEST_ELEVATION, low, Confidence.HIGH, source, excerpt, note))
            proposals.append(self._proposal(record, FieldKey.HIGHEST_ELEVATION, high, Confidence.HIGH, source, excerpt, note))
            alpine = "Yes" if isinstance(high, int) and high >= 2800 else "No" if isinstance(high, int) else None
            proposals.append(
                self._proposal(
                    record,
                    FieldKey.ALPINE_2800_2900,
                    alpine,
                    Confidence.HIGH if alpine is not None else Confidence.MEDIUM,
                    source,
                    excerpt,
                    "FOC 最高海拔 ≥ 2800 m 填 Yes；否则填 No",
                )
            )
        else:
            proposals.append(
                self._proposal(record, FieldKey.ALPINE_2800_2900, None, Confidence.MEDIUM, source, "", "FOC 未找到可用最高海拔")
            )
        flowering = self._extract_flowering(source.text)
        if flowering is not None:
            start, end, excerpt = flowering
            proposals.append(self._proposal(record, FieldKey.EARLIEST_FLOWERING, start, Confidence.HIGH, source, excerpt, "FOC 花期"))
            proposals.append(self._proposal(record, FieldKey.LATEST_FLOWERING, end, Confidence.HIGH, source, excerpt, "FOC 花期"))
        habitat = self._extract_habitat(source.text)
        if habitat is not None:
            translated = self.habitat_glossary.translate_exact(habitat)
            if translated:
                proposals.append(self._proposal(record, FieldKey.HABITAT, translated, Confidence.HIGH, source, habitat, "生境术语词典"))
            elif self.habitat_glossary.compatible_with_original(
                habitat, record.values.get(FieldKey.HABITAT.value)
            ):
                proposals.append(
                    self._proposal(
                        record,
                        FieldKey.HABITAT,
                        record.values.get(FieldKey.HABITAT.value),
                        Confidence.MEDIUM,
                        source,
                        habitat,
                        "FOC 生境与原值语义相近，保留原值",
                    )
                )
            else:
                proposals.append(self._proposal(record, FieldKey.HABITAT, None, Confidence.MEDIUM, source, habitat, "生境术语未完全命中"))
        locations, excerpt = self._extract_locations(source.text)
        if locations:
            merged = self._merge_delimited(record.values.get(FieldKey.OTHER_OCCURRENCE.value), locations)
            proposals.append(self._proposal(record, FieldKey.OTHER_OCCURRENCE, merged, Confidence.HIGH, source, excerpt, "FOC 明确分布地"))
        endemic_china, endemic_excerpt, endemic_rule = self._extract_endemic_china(source.text)
        proposals.append(
            self._proposal(
                record,
                FieldKey.ENDEMIC_CHINA,
                endemic_china,
                Confidence.MEDIUM,
                source,
                endemic_excerpt,
                endemic_rule,
            )
        )
        return proposals

    def _parse_counties(self, record: SpeciesRecord, source: SourceSection) -> tuple[FieldProposal | None, list[str], list[str]]:
        counties, unknown = self.county_index.extract(source.text)
        if not counties:
            return None, counties, unknown
        original_counties = [
            item.strip()
            for item in re.split(r"[、，,；;]", str(record.values.get(FieldKey.COUNTIES.value) or ""))
            if item.strip() and item.strip() != "-"
        ]
        standardized_original = [self.county_index.normalize(item) or item for item in original_counties]
        merged = self._merge_delimited("、".join(dict.fromkeys(standardized_original)), counties)
        proposal = self._proposal(record, FieldKey.COUNTIES, merged, Confidence.HIGH, source, "；".join(counties), "县级分布白名单合并")
        searchable = f"{record.values.get(FieldKey.COUNTIES.value) or ''}\n{source.text}"
        renamed = [
            f"{alias}→{standard}"
            for alias, standard in self.county_index.aliases.items()
            if alias != standard and alias in searchable and standard in merged
        ]
        if renamed:
            proposal.note = "；".join(f"行政区名称更新：{item}" for item in dict.fromkeys(renamed))
        return proposal, counties, unknown

    def _parse_qinling_fields(
        self,
        record: SpeciesRecord,
        source: SourceSection,
        counties: list[str],
        unknown: list[str],
    ) -> list[FieldProposal]:
        infos = self.county_index.describe(counties)
        slopes: dict[str, list[str]] = {}
        for info in infos:
            slopes.setdefault(info.slope, []).append(info.english_name)
        parts = [f"{slope} ({', '.join(names)})" for slope, names in slopes.items()]
        distribution = "; ".join(parts) if parts else None
        south_north = None
        if len(slopes) == 1:
            south_north = next(iter(slopes))
        elif len(slopes) > 1:
            south_north = "Both slopes"
        confidence = Confidence.HIGH if distribution and not unknown else Confidence.MEDIUM
        excerpt = "；".join(counties or unknown)
        proposals = [
            self._proposal(record, FieldKey.SOUTH_NORTH, south_north, confidence, source, excerpt, "按已核验秦岭县区的坡向归类"),
            self._proposal(record, FieldKey.QINLING_DISTRIBUTION, distribution, confidence, source, excerpt, "英文坡向及秦岭县区分布"),
        ]
        endemic = None
        rule = "县区范围不完整，无法安全判定秦岭特有"
        if self.county_index.has_external_province(source.text):
            endemic, rule = "No", "县级分布出现秦岭配置省份以外的记录"
        elif distribution and not unknown:
            rule = "已识别秦岭县区；仍需完整县表确认是否仅分布于秦岭"
        proposals.append(self._proposal(record, FieldKey.ENDEMIC_QINLING, endemic, Confidence.MEDIUM, source, excerpt, rule))
        return proposals

    @staticmethod
    def _label_value(text: str, label: str) -> str | None:
        match = re.search(rf"^{label}[：:]\s*(.+)$", text, re.MULTILINE)
        return match.group(1).strip() if match else None

    @staticmethod
    def _strip_author(name: str) -> str | None:
        match = re.match(r"^([A-Z][A-Za-z-]+\s+[a-z-]+(?:\s+(?:subsp\.|var\.|f\.)\s+[a-z-]+)?)\b", name)
        return match.group(1) if match else None

    @staticmethod
    def _extract_elevation(text: str) -> tuple[int, int | str, str, str] | None:
        normalized = re.sub(r"\s+", " ", text)
        habitat_cues = ("slope", "forest", "thicket", "valley", "mountain", "grassland", "meadow", "river")
        for match in re.finditer(r"(\d{2,4})\s*(?:-|–|—|to)\s*(\d{2,4})\s*m\b", normalized, re.IGNORECASE):
            context = normalized[max(0, match.start() - 90):match.end() + 20].casefold()
            if any(cue in context for cue in habitat_cues):
                low, high = int(match.group(1)), int(match.group(2))
                if low <= high:
                    return low, high, match.group(0), "FOC 完整海拔范围"
        for pattern, kind in ((r"\b(?:below|under|less than)\s+(\d{2,4})\s*m\b", "below"), (r"\b(?:above|over|higher than)\s+(\d{2,4})\s*m\b", "above"), (r"\bat\s+(\d{2,4})\s*m\b", "exact")):
            match = re.search(pattern, normalized, re.IGNORECASE)
            if not match:
                continue
            value = int(match.group(1))
            if kind == "below":
                return 1, value, match.group(0), f"FOC 单边上限：{match.group(0)}；按规则更新为 1–{value} m"
            if kind == "above":
                return value, "-", match.group(0), f"FOC 单边下限：{match.group(0)}；按规则更新为 {value}–- m"
            return value, value, match.group(0), f"FOC 精确海拔：{match.group(0)}；按规则更新为 {value}–{value} m"
        return None

    @staticmethod
    def _extract_flowering(text: str) -> tuple[int, int, str] | None:
        match = re.search(
            r"\bfl\.\s*([A-Za-z]+)\.?\s*(?:[-–—]\s*([A-Za-z]+)\.?)?\s*(?:\(\s*[-–—]\s*([A-Za-z]+)\.??\s*\))?",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        names = [item for item in match.groups() if item]
        try:
            months = [MONTHS[name.rstrip(".").casefold()] for name in names]
        except KeyError:
            return None
        return months[0], months[-1], match.group(0)

    @staticmethod
    def _extract_habitat(text: str) -> str | None:
        for sentence in re.split(r"[.!?]\s*", re.sub(r"\s+", " ", text)):
            fragment = sentence.split(";")[0].strip(" ,")
            lowered = fragment.casefold()
            if any(word in lowered for word in ("forest", "thicket", "slope", "valley", "meadow", "grassland", "riverbank")):
                if len(fragment) <= 120:
                    return fragment
        return None

    @staticmethod
    def _extract_locations(text: str) -> tuple[list[str], str]:
        candidates: list[tuple[list[str], str]] = []
        for sentence in re.split(r"[.!?]\s*", re.sub(r"\s+", " ", text)):
            matches: list[tuple[int, str]] = []
            for english, chinese in sorted(PLACE_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
                for match in re.finditer(rf"\b{re.escape(english)}\b", sentence, re.IGNORECASE):
                    matches.append((match.start(), chinese))
            found: list[str] = []
            for _, chinese in sorted(matches):
                if chinese not in found:
                    found.append(chinese)
            if len(found) >= 2:
                candidates.append((found, sentence.strip()))
        return candidates[-1] if candidates else ([], "")

    @staticmethod
    def _extract_endemic_china(text: str) -> tuple[str | None, str, str]:
        """依据 FOC 分布句中的国家／地区名称保守判断中国特有性。"""
        for sentence in re.split(r"[.!?]\s*", re.sub(r"\s+", " ", text)):
            matched = [
                place for place in PLACE_NAMES
                if re.search(rf"\b{re.escape(place)}\b", sentence, re.IGNORECASE)
            ]
            if not matched:
                continue
            foreign = [place for place in matched if place not in CHINA_LOCATION_TERMS]
            if foreign:
                return "No", sentence.strip(), f"FOC 分布包含境外地区：{', '.join(foreign)}"
        endemic_match = re.search(r"\bendemic\s+(?:to|in)\s+China\b[^.?!]*", text, re.IGNORECASE)
        if endemic_match:
            return "Yes", endemic_match.group(0).strip(), "FOC 明确标注 endemic to/in China"
        return None, "", "FOC 未明确标注中国特有，不能仅凭中国省份列表判定"

    def _ensure_all_fields(self, record: SpeciesRecord, snapshot: SourceSnapshot, result: ParseResult) -> None:
        """每一个业务列都展示网站证据或明确的待复核原因。"""
        covered = {proposal.field for proposal in result.proposals}
        county_fields = {
            FieldKey.COUNTIES,
            FieldKey.ENDEMIC_QINLING,
            FieldKey.SOUTH_NORTH,
            FieldKey.QINLING_DISTRIBUTION,
        }
        for field in FieldKey:
            if field in covered:
                continue
            source = snapshot.county_distribution if field in county_fields else snapshot.foc
            rule = "网站未提供可直接解析的证据，保留原值并待复核"
            if not source.usable:
                rule = "对应网站板块未成功加载，保留原值并待复核"
            proposal = self._proposal(
                record, field, None, Confidence.NONE, source,
                source.text[:240] if source.usable else "", rule,
            )
            if not source.usable:
                proposal.state = ReviewState.FAILED
            result.proposals.append(proposal)

    @staticmethod
    def _merge_delimited(original: object, additions: list[str]) -> str:
        values: list[str] = []
        if original not in (None, "", "-"):
            values.extend(item.strip() for item in re.split(r"[、，,；;]", str(original)) if item.strip())
        for item in additions:
            if item not in values:
                values.append(item)
        return "、".join(values)

    @staticmethod
    def _proposal(
        record: SpeciesRecord,
        field: FieldKey,
        suggestion: object,
        confidence: Confidence,
        source: SourceSection,
        excerpt: str,
        rule: str,
    ) -> FieldProposal:
        proposal = FieldProposal(
            worksheet_name=record.worksheet_name,
            excel_row=record.excel_row,
            species_name=record.latin_name,
            field=field,
            original_value=record.values.get(field.value),
            suggested_value=suggestion,
            confidence=confidence,
            state=ReviewState.PENDING_REVIEW,
            source_name=(
                "iPlant 物种县级分布"
                if field is FieldKey.COUNTIES
                else "iPlant 名称分类（FOC 标题缺失时供复核）"
                if "?t=n" in (source.resolved_url or source.requested_url)
                else "iPlant 中国植物志（修订版，FOC）"
            ),
            source_url=source.resolved_url or source.requested_url,
            source_excerpt=excerpt,
            parser_rule=rule,
            note=rule,
        )
        if suggestion is None:
            return proposal
        if str(suggestion).strip() == str(proposal.original_value or "").strip():
            proposal.final_value = proposal.original_value
            proposal.state = ReviewState.NO_CHANGE
        elif confidence is Confidence.HIGH:
            proposal.set_auto_ready()
        return proposal
