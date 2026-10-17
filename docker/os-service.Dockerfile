# Build a partir da raiz do mono repo (precisa ver os-service/ e o submódulo
# shared-kernel/ já inicializado dentro dele: git submodule update --init --recursive).
# O Dockerfile original do os-service não copia shared-kernel/ — ele só existe
# desde que o shared-kernel passou a ser consumido via submódulo, então mantemos
# esta variante aqui no mono em vez de alterar o repo do os-service.
FROM maven:3-amazoncorretto-21 AS build

WORKDIR /app

COPY os-service/pom.xml ./
COPY os-service/shared-kernel ./shared-kernel
RUN mvn dependency:go-offline -q

COPY os-service/src ./src
RUN mvn clean package -DskipTests

FROM amazoncorretto:21-alpine3.20

WORKDIR /app

COPY --from=build /app/target/*.jar ./app.jar

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "app.jar"]
