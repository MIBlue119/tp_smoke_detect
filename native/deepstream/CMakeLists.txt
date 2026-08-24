cmake_minimum_required(VERSION 3.20)
project(tp_smoke_detect_media_worker VERSION 0.1.0 LANGUAGES CXX)

option(TP_SMOKE_DETECT_ENABLE_DEEPSTREAM
       "Build the NVIDIA DeepStream adapter (requires a qualified SDK)" OFF)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(media_worker
  src/candidate.cpp
  src/deepstream_pipeline.cpp
  src/media_worker.cpp
)
target_include_directories(media_worker PUBLIC include)
target_compile_options(media_worker PRIVATE -Wall -Wextra -Wpedantic -Werror)

if(TP_SMOKE_DETECT_ENABLE_DEEPSTREAM)
  if(NOT DEFINED DEEPSTREAM_SDK_ROOT)
    message(FATAL_ERROR
      "TP_SMOKE_DETECT_ENABLE_DEEPSTREAM requires -DDEEPSTREAM_SDK_ROOT=/opt/nvidia/deepstream")
  endif()
  if(NOT EXISTS "${DEEPSTREAM_SDK_ROOT}")
    message(FATAL_ERROR "DeepStream SDK root does not exist: ${DEEPSTREAM_SDK_ROOT}")
  endif()
  find_package(PkgConfig REQUIRED)
  pkg_check_modules(GSTREAMER REQUIRED IMPORTED_TARGET gstreamer-1.0)
  pkg_check_modules(GSTREAMER_VIDEO REQUIRED IMPORTED_TARGET gstreamer-video-1.0)
  find_path(DEEPSTREAM_INCLUDE_DIR gstnvdsmeta.h
    PATHS "${DEEPSTREAM_SDK_ROOT}/sources/includes" NO_DEFAULT_PATH REQUIRED)
  find_path(DEEPSTREAM_INFER_INCLUDE_DIR nvdsinfer.h
    PATHS "${DEEPSTREAM_SDK_ROOT}/sources/includes" NO_DEFAULT_PATH REQUIRED)
  find_library(DEEPSTREAM_META_LIBRARY nvdsgst_meta
    PATHS "${DEEPSTREAM_SDK_ROOT}/lib" NO_DEFAULT_PATH REQUIRED)
  target_compile_definitions(media_worker PUBLIC TP_SMOKE_DETECT_ENABLE_DEEPSTREAM=1)
  target_include_directories(media_worker PRIVATE
    "${DEEPSTREAM_INCLUDE_DIR}" "${DEEPSTREAM_INFER_INCLUDE_DIR}")
  target_link_libraries(media_worker PRIVATE
    PkgConfig::GSTREAMER PkgConfig::GSTREAMER_VIDEO "${DEEPSTREAM_META_LIBRARY}")
else()
  target_compile_definitions(media_worker PUBLIC TP_SMOKE_DETECT_ENABLE_DEEPSTREAM=0)
endif()

include(CTest)
if(BUILD_TESTING)
  add_executable(media_worker_tests tests/media_worker_tests.cpp)
  target_link_libraries(media_worker_tests PRIVATE media_worker)
  target_compile_options(media_worker_tests PRIVATE -Wall -Wextra -Wpedantic -Werror)
  add_test(NAME media_worker_tests COMMAND media_worker_tests)
endif()

add_executable(media_publisher src/media_publisher.cpp)
target_link_libraries(media_publisher PRIVATE media_worker)
target_compile_options(media_publisher PRIVATE -Wall -Wextra -Wpedantic -Werror)
