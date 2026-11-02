idf_component_register(SRCS "vendor/opendroneid.c" INCLUDE_DIRS "vendor")
target_compile_definitions(${COMPONENT_LIB} PUBLIC ODID_DISABLE_PRINTF)
target_compile_options(${COMPONENT_LIB} PRIVATE -ffunction-sections -fdata-sections)
