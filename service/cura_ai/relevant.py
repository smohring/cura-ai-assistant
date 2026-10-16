"""Settings that are routinely tuned; their limits and current values go into the prompt."""

RELEVANT_KEYS = [
    # quality and shell
    "layer_height", "layer_height_0", "line_width", "wall_line_width_0", "wall_line_count",
    "wall_thickness", "top_layers", "bottom_layers", "top_bottom_thickness", "roofing_layer_count",
    "optimize_wall_printing_order", "z_seam_type", "z_seam_corner", "xy_offset", "skin_overlap",
    "fill_outline_gaps", "slicing_tolerance", "adaptive_layer_height_enabled",
    # infill
    "infill_sparse_density", "infill_pattern", "infill_before_walls", "infill_wall_line_count",
    "gradual_infill_steps", "infill_overlap", "infill_line_width",
    # material
    "material_print_temperature", "material_print_temperature_layer_0", "material_bed_temperature",
    "material_bed_temperature_layer_0", "material_flow", "material_shrinkage_percentage",
    # speed, acceleration
    "speed_print", "speed_infill", "speed_wall_0", "speed_wall_x", "speed_topbottom",
    "speed_travel", "speed_print_layer_0", "speed_support", "acceleration_enabled", "jerk_enabled",
    # travel, retraction
    "retraction_enable", "retraction_amount", "retraction_speed", "retraction_hop_enabled",
    "retraction_hop", "retraction_combing", "travel_avoid_other_parts",
    "travel_retract_before_outer_wall", "coasting_enable",
    # cooling
    "cool_fan_enabled", "cool_fan_speed", "cool_fan_speed_min", "cool_fan_full_layer",
    "cool_min_layer_time", "cool_min_speed",
    # support
    "support_enable", "support_structure", "support_type", "support_angle", "support_pattern",
    "support_infill_rate", "support_z_distance", "support_xy_distance", "support_top_distance",
    "support_bottom_distance", "support_interface_enable", "support_roof_enable",
    "conical_overhang_enabled", "bridge_settings_enabled",
    # adhesion
    "adhesion_type", "brim_width", "brim_line_count", "skirt_line_count", "raft_margin",
    # surface effects
    "ironing_enabled", "magic_fuzzy_skin_enabled", "draft_shield_enabled", "ooze_shield_enabled",
    # surface quality of walls and skin
    "wall_0_inset", "wall_0_wipe_dist", "wall_x_extruder_nr",
    "top_bottom_pattern", "top_bottom_pattern_0", "roofing_pattern", "roofing_monotonic",
    "skin_monotonic", "skin_outline_count", "skin_preshrink", "expand_skins_expand_distance",
    "ironing_pattern", "ironing_line_spacing", "ironing_flow", "ironing_inset", "speed_ironing",
    "z_seam_x", "z_seam_y", "z_seam_position", "z_seam_relative",
    "acceleration_wall_0", "acceleration_wall", "acceleration_topbottom", "acceleration_print",
    "acceleration_support", "acceleration_travel", "jerk_wall_0", "jerk_topbottom", "jerk_print",
    "speed_layer_0", "speed_slowdown_layers",
    "cool_fan_speed_max", "cool_min_layer_time_fan_speed_max", "cool_lift_head",
    # support, its interface (roof and floor) and what touches it
    "support_extruder_nr", "support_wall_count", "support_brim_enable", "support_join_distance",
    "support_offset", "support_interface_height", "support_roof_height", "support_bottom_height",
    "support_interface_density", "support_roof_density", "support_bottom_density",
    "support_interface_pattern", "support_roof_pattern", "support_bottom_pattern",
    "support_bottom_enable", "support_fan_enable",
    "support_line_width", "support_interface_line_width", "support_roof_line_width",
    "speed_support_interface", "speed_support_roof", "speed_support_bottom",
    "support_conical_enabled", "support_conical_angle",
    "support_conical_min_width", "minimum_support_area", "minimum_roof_area",
    "minimum_bottom_area", "support_tree_angle", "support_tree_branch_diameter",
    "support_tree_max_diameter", "support_xy_overrides_z", "support_use_towers",
    # retraction and travel details
    "retraction_prime_speed", "retraction_extra_prime_amount", "retraction_min_travel",
    "retraction_count_max", "retraction_combing_max_distance", "travel_avoid_distance",
    "retraction_hop_only_when_collides",
    "layer_start_x", "layer_start_y", # temperatures and flow around the surface
    "material_initial_print_temperature", "material_final_print_temperature",
    "material_flow_layer_0", "material_standby_temperature",
]  # fmt: skip
