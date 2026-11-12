set(kritapsfilters_SOURCES ps_filters.cpp ps_axis_lock.cpp ps_engine_control.cpp)
kis_add_library(kritapsfilters MODULE ${kritapsfilters_SOURCES})
target_link_libraries(kritapsfilters kritaui)
install(TARGETS kritapsfilters DESTINATION ${KRITA_PLUGIN_INSTALL_DIR})
