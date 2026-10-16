source_players
                            ) * 28 + 55
                        )
                    )

                    players = [
                        player.strip()
                        for player in (
                            raw.splitlines()
                        )
                        if player.strip()
                    ]

                    total_players += (
                        len(players)
                    )

                    st.caption(
                        f"{len(players)} players · "
                        f"[Verify on PlayCricket]"
                        f"({row['url']})"
                    )

                    with st.expander(
                        "Technical player list sources"
                    ):
                        st.json([
                            {
                                "source": c["source"],
                                "players": c["players"]
                            }
                            for c in candidates
                        ])

                    edited_rows.append({
                        **row,
                        "players": players
                    })

            output = (
                selection_output(
                    edited_rows
                )
            )

            st.divider()

            st.markdown(
                "### Combined Team Announcement"
            )

            st.caption(
                f"{len(edited_rows)} teams · "
                f"{total_players} players"
            )

            st.code(
                output,
                language=None,
                wrap_lines=True
            )

            with st.expander(
                "Edit or copy announcement"
            ):
                if (
                    st.session_state.get(
                        "combined_source"
                    )
                    != output
                ):
                    st.session_state[
                        "combined_edit"
                    ] = output

                    st.session_state[
                        "combined_source"
                    ] = output

                st.text_area(
                    "Copy-ready selections",
                    key="combined_edit",
                    height=450
                )

            st.download_button(
                "Download Team Selections",
                st.session_state.get(
                    "combined_edit",
                    output
                ),
                "hbcc_team_selections.txt",
                "text/plain",
                use_container_width=True
            )

            st.caption(
                "Verify player lists and captains "
                "before publishing."
            )


# ============================================================
# DIAGNOSTICS AND FOOTER
# ============================================================

if st.session_state.diagnostics:
    with st.sidebar.expander(
        "PlayCricket search diagnostics"
    ):
        st.json(
            st.session_state.diagnostics
        )

        st.json(
            st.session_state.schedule_samples
        )

st.divider()

st.caption(
    "HBCC Content Studio · Built for the Hawks · "
    "PlayCricket data should be verified before publishing."
)
