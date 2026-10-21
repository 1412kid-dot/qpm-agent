# 验证范围与解释规则

查询 qpm_capabilities.validation 获取简明验证范围。插件当前已实现范围内的工作流完成初步验收；代表性物理检查通过，但不代表所有材料、晶向、几何及非线性过程均已验证。

插件能力范围之外的语义任务和更完整的自主研究流程属于后续版本扩展范围。用户应明确材料、切向、模式、传播方向、波长角色、目标指标和工艺限制；缺少依据的物理量不能由模型补造。扩展能力与物理适用性需独立说明并验收。

- Distinguish uncompensated delta_beta from residual delta_beta-K_m.
- solver_calls and cache_hits are separate counters; quote both as recorded.
- Generic failure/low overlap/finite peak offset does not establish a physical cause; inspect actual run evidence.
- SPDC normalized_efficiency and process_coefficient are unavailable; query task metric_availability.
- Adding GVD/GVM requires derivative points and a fresh estimate; period-only budget does not cover them.
- Role-specific beta1 need not remain invariant when polarizations are exchanged.
- For identical nonzero order, equal finite periods require equal absolute mismatches, including opposite signs.
- K_m remains defined; at 50% duty the nonzero even-order Fourier coefficient vanishes, not K_m.
