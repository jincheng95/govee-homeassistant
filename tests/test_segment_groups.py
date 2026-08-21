"""Tests for `segment_groups.py` — the `Name: indices; Name: indices` parser.

Organised around the grammar (ranges, comma lists, mixed) and every rejection
path the options-flow step needs a distinct field error for.
"""

from __future__ import annotations

import pytest

from custom_components.govee.segment_groups import (
    SegmentGroupsError,
    format_segment_groups,
    group_suffix,
    parse_segment_groups,
    slugify_group_name,
)


class TestGrammar:
    def test_a_dash_range_is_expanded_and_zero_based(self):
        assert parse_segment_groups("Left: 1-5", segment_count=10) == {"Left": [0, 1, 2, 3, 4]}

    def test_a_comma_list_is_zero_based(self):
        assert parse_segment_groups("Corners: 1,3,5", segment_count=10) == {"Corners": [0, 2, 4]}

    def test_ranges_and_lists_mix_in_one_group(self):
        assert parse_segment_groups("Mixed: 1-3,7,9-10", segment_count=10) == {"Mixed": [0, 1, 2, 6, 8, 9]}

    def test_multiple_groups_separated_by_semicolons(self):
        assert parse_segment_groups("Left: 1-5; Right: 6-10", segment_count=10) == {
            "Left": [0, 1, 2, 3, 4],
            "Right": [5, 6, 7, 8, 9],
        }

    def test_a_reversed_range_is_normalised(self):
        assert parse_segment_groups("Left: 5-1", segment_count=10) == {"Left": [0, 1, 2, 3, 4]}

    def test_whitespace_around_names_and_tokens_is_trimmed(self):
        assert parse_segment_groups("  Left  :  1 , 2 - 3  ", segment_count=10) == {"Left": [0, 1, 2]}

    def test_a_single_index_group(self):
        assert parse_segment_groups("Solo: 7", segment_count=10) == {"Solo": [6]}

    def test_groups_preserve_definition_order(self):
        result = parse_segment_groups("C: 1; A: 2; B: 3", segment_count=10)
        assert list(result.keys()) == ["C", "A", "B"]


class TestRejections:
    def test_zero_groups(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("", segment_count=10)
        assert excinfo.value.code == "segment_groups_zero_groups"

    def test_zero_groups_when_only_whitespace_and_separators(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("  ; ; ", segment_count=10)
        assert excinfo.value.code == "segment_groups_zero_groups"

    def test_missing_colon_is_invalid_syntax(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left 1-5", segment_count=10)
        assert excinfo.value.code == "segment_groups_invalid_syntax"

    def test_non_numeric_index_is_invalid_syntax(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: one-five", segment_count=10)
        assert excinfo.value.code == "segment_groups_invalid_syntax"

    def test_empty_name(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups(": 1-5", segment_count=10)
        assert excinfo.value.code == "segment_groups_empty_name"

    def test_duplicate_name(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: 1-2; Left: 3-4", segment_count=10)
        assert excinfo.value.code == "segment_groups_duplicate_name"

    def test_empty_group(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: ", segment_count=10)
        assert excinfo.value.code == "segment_groups_empty_group"

    def test_overlap_across_groups(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: 1-5; Right: 5-10", segment_count=10)
        assert excinfo.value.code == "segment_groups_overlap"

    def test_overlap_within_one_group(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: 1,1,2", segment_count=10)
        assert excinfo.value.code == "segment_groups_overlap"

    def test_index_below_one_is_out_of_range(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: 0", segment_count=10)
        assert excinfo.value.code == "segment_groups_out_of_range"

    def test_index_above_segment_count_is_out_of_range(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: 11", segment_count=10)
        assert excinfo.value.code == "segment_groups_out_of_range"

    def test_a_range_end_above_segment_count_is_out_of_range(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Left: 8-12", segment_count=10)
        assert excinfo.value.code == "segment_groups_out_of_range"

    def test_more_than_sixteen_segments_in_one_group(self):
        with pytest.raises(SegmentGroupsError) as excinfo:
            parse_segment_groups("Everything: 1-17", segment_count=20)
        assert excinfo.value.code == "segment_groups_too_large"

    def test_exactly_sixteen_segments_is_allowed(self):
        result = parse_segment_groups("Everything: 1-16", segment_count=20)
        assert result["Everything"] == list(range(16))


class TestFormatRoundTrip:
    def test_format_then_parse_recovers_the_same_groups(self):
        original = {"Left": [0, 1, 2, 3, 4], "Right": [5, 6, 7, 8, 9]}
        text = format_segment_groups(original)
        assert parse_segment_groups(text, segment_count=10) == original

    def test_format_collapses_consecutive_indices_into_a_range(self):
        assert format_segment_groups({"Left": [0, 1, 2, 3, 4]}) == "Left: 1-5"

    def test_format_of_no_groups_is_empty(self):
        assert format_segment_groups({}) == ""

    def test_format_keeps_non_consecutive_indices_as_a_list(self):
        assert format_segment_groups({"Corners": [0, 2, 4]}) == "Corners: 1,3,5"


class TestSlugAndSuffix:
    def test_slug_lowercases_and_replaces_spaces(self):
        assert slugify_group_name("Left Bar") == "left_bar"

    def test_slug_collapses_punctuation(self):
        assert slugify_group_name("Left / Right!") == "left_right"

    def test_an_all_punctuation_name_still_yields_a_slug(self):
        assert slugify_group_name("!!!") == "group"

    def test_group_suffix_uses_the_slug(self):
        assert group_suffix("Left Bar") == "_segment_group_left_bar"
