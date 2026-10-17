FROM maven:3-amazoncorretto-21 AS build

WORKDIR /app

COPY inventory-service/pom.xml ./
COPY inventory-service/shared-kernel ./shared-kernel
RUN mvn dependency:go-offline -q

COPY inventory-service/src ./src
RUN mvn clean package -DskipTests

FROM amazoncorretto:21-alpine3.20

WORKDIR /app

COPY --from=build /app/target/*.jar ./app.jar

EXPOSE 8080

ENTRYPOINT ["java", "-jar", "app.jar"]
