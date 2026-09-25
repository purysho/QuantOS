                (
                    zero_quote("1Y", "-0.01"),
                    zero_quote("2Y", "-0.005"),
                )
            ),
            policy=policy(),
        )
        self.assertGreater(
            curve.pillars[0].discount_factor,
            Decimal("1"),
        )
        self.assertTrue(curve.quantlib_verified)

    def test_log_linear_interpolation_matches_reference(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(
                (
                    zero_quote("1Y", "0.02"),
                    zero_quote("2Y", "0.04"),
                )
            ),
            policy=policy(),
        )
        one = curve.pillars[0]
        two = curve.pillars[1]
        midpoint_days = (
            one.pillar_date
            + (two.pillar_date - one.pillar_date) // 2
        )
        target_t = Decimal(
            (midpoint_days - curve.valuation_date).days
        ) / Decimal("365")
        weight = (
            (target_t - one.year_fraction)
            / (two.year_fraction - one.year_fraction)
        )
        expected_log = (
            math.log(float(one.discount_factor))
            + float(weight)
            * (
                math.log(float(two.discount_factor))
                - math.log(float(one.discount_factor))
            )
        )
        expected = math.exp(expected_log)
        observed = float(curve.discount_factor(midpoint_days))
        self.assertAlmostEqual(observed, expected, places=12)

    def test_extrapolation_fails_closed_by_default(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(),
            policy=policy(),
        )
        with self.assertRaises(ValueError):
            curve.discount_factor(
                curve.pillars[-1].pillar_date.replace(
                    year=curve.pillars[-1].pillar_date.year + 1
                )
            )

    def test_extreme_zero_rate_fails_policy_sanity_bound(self):
        with self.assertRaises(ValueError):
            DiscountCurveBuilder().build(
                snapshot=snapshot(
                    (
                        zero_quote("1Y", "0.03"),
                        zero_quote("2Y", "1.50"),
                    )
                ),
                policy=policy(
                    maximum_absolute_zero_rate=Decimal("1")
                ),
            )

    def test_extreme_implied_forward_rate_fails_closed(self):
        with self.assertRaises(ValueError):
            DiscountCurveBuilder().build(
                snapshot=snapshot(
                    (
                        zero_quote("1Y", "-0.50"),
                        zero_quote("2Y", "0.50"),
                    )
                ),