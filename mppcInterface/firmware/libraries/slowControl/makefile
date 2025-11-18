CXX = g++
CXXFLAGS = -std=c++11 -I.
LDLIBS = -lwiringPi

HEADERS = shiftRegister.h dac60508.h ice40.h
OBJECTS = main.o

default: main

main: $(OBJECTS)
		$(CXX) -o $@ $(LDLIBS) $^

%.o: ./%.cpp
		$(CXX) -c $< $(CXXFLAGS)

clean:
		-rm -f $(OBJECTS)
		-rm -f main
