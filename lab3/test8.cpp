#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <string.h>
#include <unistd.h>

#include <iostream>
#include <string>

using namespace std;

int main(){
  int s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
  if(s < 0) return 1;

  struct sockaddr_in sin;

  // bind()
  memset(&sin, 0, sizeof(sin));
  sin.sin_family = AF_INET;
  sin.sin_addr.s_addr = INADDR_ANY;
  sin.sin_port = htons(10000 + 113);
  if (bind(s, (struct sockaddr *) &sin, sizeof(sin)) < 0){
    cerr << strerror(errno) << endl;
    return 1;
  }

  while(true){
    int numBytes;
    char buf[65536] = {0};
    memset(&sin, 0, sizeof(sin));
    socklen_t sin_size = sizeof(sin);

    // recvfrom
    numBytes = recvfrom(s, buf, sizeof(buf), 0, (struct sockaddr *) &sin, &sin_size);
    if(numBytes < 0){
      cerr << strerror(errno) << endl;
      return 1;
    }
    buf[numBytes] = '\0';

    // sendto
    numBytes = sendto(s, buf, numBytes, 0, (struct sockaddr *) &sin, sizeof(sin));
    if(numBytes < 0){
      cerr << strerror(errno) << endl;
      return 1;
    }
  }
  
  close(s);
  return 0;
}