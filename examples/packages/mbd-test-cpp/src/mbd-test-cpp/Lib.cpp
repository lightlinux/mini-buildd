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
	// Test that SHM works in build environment
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

	void mbd_test_cpp()
	{
		std::cout << "Test project mbd-test-cpp." << std::endl;
		mbd_test_cpp_shm();
	}
}
