// Project config
#include "config.h"

// Implementation
#include "Lib.hpp"

// STDC++
#include <iostream>
#include <cstring>
#include <cerrno>
#include <cstdlib>

// System C
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>

namespace MBD_TEST_CPP
{
	// Test: /dev/shm in build environment
	void mbd_test_cpp_shm()
	{
		int shm_fd(::shm_open("mbd-test-cpp", O_RDWR | O_CREAT, S_IRWXU));
		if (shm_fd < 0)
		{
			std::cerr << "Ooops: shm_open(2) failed: " << std::strerror(errno) << std::endl;
			std::exit(1);
		}
		std::cout << "OK: shm_open works." << std::endl;
	}

	// Test: Buildlog with (some) non-UTF-8 encoding
	void mbd_test_cpp_non_utf8_output()
	{
		std::cout << "UTF-8 : Ã¶Ã¤" << std::endl;
		std::cout << "Latin1: öä" << std::endl;
	}

	void mbd_test_cpp()
	{
		std::cout << "Test project mbd-test-cpp." << std::endl;
		mbd_test_cpp_shm();
		mbd_test_cpp_non_utf8_output();
	}
}
